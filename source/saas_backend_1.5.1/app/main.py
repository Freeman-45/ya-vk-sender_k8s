from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from sqlalchemy import delete
from jose import JWTError, jwt
from passlib.context import CryptContext
from cryptography.fernet import Fernet
from pydantic import BaseModel
import httpx
import os
import json
import logging
import urllib.parse

from app.models import Base, User, YandexForm, VkBot, Route

# --- КОНФИГУРАЦИЯ ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://saas_user:saas_password@db:5432/saas_db")
SECRET_KEY = os.getenv("SECRET_KEY", "super_secret_jwt_key")
ALGORITHM = "HS256"
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "8jR9uZ0LqOqA_8G5_G5_G5_G5_G5_G5_G5_G5_G5_G4=")
ALLOW_REGISTRATION = os.getenv("ALLOW_REGISTRATION", "false").lower() == "true"

fernet = Fernet(ENCRYPTION_KEY.encode())

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
app = FastAPI(title="Yandex-VK SaaS Gateway")

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- МОДЕЛИ PYDANTIC ---
class RouteUpdate(BaseModel):
    target_vk_id: str
    is_active: bool

class YandexLogin(BaseModel):
    email: str

class UserCreate(BaseModel):
    email: str
    password: str
    is_admin: bool = False

# --- БАЗА ДАННЫХ ---
async def get_db():
    async with async_session() as session:
        yield session

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Автоматическое создание первого администратора
    async with async_session() as db:
        result = await db.execute(select(User).where(User.email == "kiba@test.ru"))
        admin = result.scalars().first()
        if not admin:
            new_admin = User(
                email="kiba@test.ru", 
                hashed_password=get_password_hash("admin"),
                is_admin=True
            )
            db.add(new_admin)
            await db.commit()
            logger.info("Создан первый администратор kiba@test.ru (пароль: admin)")

# --- АУТЕНТИФИКАЦИЯ ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

@app.post("/register")
async def register(email: str, password: str, db: AsyncSession = Depends(get_db)):
    if not ALLOW_REGISTRATION:
        raise HTTPException(status_code=403, detail="Публичная регистрация закрыта. Обратитесь к администратору.")
    
    result = await db.execute(select(User).where(User.email == email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    new_user = User(email=email, hashed_password=get_password_hash(password), is_admin=False)
    db.add(new_user)
    await db.commit()
    return {"status": "ok"}

@app.post("/token")
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalars().first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    
    token = jwt.encode({"sub": str(user.id)}, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}

@app.post("/auth/yandex")
async def yandex_auth(data: YandexLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(status_code=403, detail="Пользователь с таким email не найден. Обратитесь к администратору.")
    
    token = jwt.encode({"sub": str(user.id)}, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": token, "token_type": "bearer"}

async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid token")
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# --- УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ (ТОЛЬКО ДЛЯ АДМИНОВ) ---
@app.get("/users/me")
async def get_me(user: User = Depends(get_current_user)):
    return {"email": user.email, "is_admin": user.is_admin}

@app.get("/users")
async def get_users(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Доступ только для администраторов")
    result = await db.execute(select(User))
    return [{"id": u.id, "email": u.email, "is_admin": u.is_admin} for u in result.scalars().all()]

@app.post("/users")
async def create_user(data: UserCreate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Доступ только для администраторов")
    
    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Пользователь с таким email уже существует")
    
    new_user = User(
        email=data.email, 
        hashed_password=get_password_hash(data.password),
        is_admin=data.is_admin
    )
    db.add(new_user)
    await db.commit()
    return {"status": "ok", "id": new_user.id}

# --- CRUD: БОТЫ ---
@app.post("/bots")
async def create_bot(name: str, group_id: str, vk_token: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    encrypted = fernet.encrypt(vk_token.encode()).decode()
    bot = VkBot(name=name, group_id=group_id, encrypted_token=encrypted, owner_id=user.id)
    db.add(bot)
    await db.commit()
    return {"id": bot.id, "name": bot.name}

@app.get("/bots")
async def get_bots(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(VkBot).where(VkBot.owner_id == user.id))
    return [{"id": b.id, "name": b.name, "group_id": b.group_id} for b in result.scalars().all()]

@app.delete("/bots/{bot_id}")
async def delete_bot(bot_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(VkBot).where(VkBot.id == bot_id, VkBot.owner_id == user.id))
    bot = result.scalars().first()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    await db.execute(delete(Route).where(Route.bot_id == bot.id))
    await db.delete(bot)
    await db.commit()
    return {"status": "ok"}

# --- CRUD: ФОРМЫ ---
@app.post("/forms")
async def create_form(name: str, survey_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    form = YandexForm(name=name, survey_id=survey_id, owner_id=user.id)
    db.add(form)
    await db.commit()
    await db.refresh(form)
    return form

@app.get("/forms")
async def get_forms(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(YandexForm).where(YandexForm.owner_id == user.id))
    return result.scalars().all()

@app.delete("/forms/{form_id}")
async def delete_form(form_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(YandexForm).where(YandexForm.id == form_id, YandexForm.owner_id == user.id))
    form = result.scalars().first()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")
    await db.execute(delete(Route).where(Route.form_id == form.id))
    await db.delete(form)
    await db.commit()
    return {"status": "ok"}

# --- CRUD: МАРШРУТЫ ---
@app.post("/routes")
async def create_route(form_id: int, bot_id: int, target_vk_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    route = Route(form_id=form_id, bot_id=bot_id, target_vk_id=target_vk_id, owner_id=user.id)
    db.add(route)
    await db.commit()
    await db.refresh(route)
    return route

@app.get("/routes")
async def get_routes(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Route).where(Route.owner_id == user.id))
    return result.scalars().all()

@app.put("/routes/{route_id}")
async def update_route(route_id: int, route_data: RouteUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Route).where(Route.id == route_id, Route.owner_id == user.id))
    route = result.scalars().first()
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    route.target_vk_id = route_data.target_vk_id
    route.is_active = route_data.is_active
    await db.commit()
    return {"status": "ok"}

@app.delete("/routes/{route_id}")
async def delete_route(route_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Route).where(Route.id == route_id, Route.owner_id == user.id))
    route = result.scalars().first()
    if not route:
        raise HTTPException(status_code=404, detail="Route not found")
    await db.delete(route)
    await db.commit()
    return {"status": "ok"}

# --- ВЕБХУКИ И ОТПРАВКА ---
@app.post("/webhook/{form_id}")
async def yandex_webhook(form_id: int, request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(YandexForm).where(YandexForm.id == form_id))
    form = result.scalars().first()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    # --- ЧТЕНИЕ ТЕЛА ЗАПРОСА ---
    payload = {}
    try:
        raw_body = await request.body()
        body_str = raw_body.decode('utf-8')
        
        # 1. Убираем экранирование кавычек
        clean_str = body_str.replace('\\"', '"')
        
        # 2. Пытаемся распарсить стандартно
        try:
            payload = json.loads(clean_str)
        except json.JSONDecodeError:
            # 3. Если не вышло, значит, внутри текста есть неэкранированные кавычки или переносы.
            # Используем более мягкий парсер, который игнорирует ошибки
            logger.warning("Стандартный парсинг не удался, используем мягкий парсер...")
            import ast
            try:
                # Пытаемся распарсить как Python-словарь (он прощает лишние кавычки)
                payload = ast.literal_eval(clean_str)
            except Exception:
                # 4. Если и это не вышло, пробуем "грязный" регулярный парсинг
                import re
                logger.warning("Используем регулярные выражения для вытаскивания данных...")
                payload = {}
                # Ищем все пары "ключ": "значение" (учитывая Unicode)
                pattern = r'"([^"]+)":\s*"([^"]*)"'
                matches = re.findall(pattern, clean_str)
                if matches:
                    for k, v in matches:
                        # Декодируем Unicode
                        try:
                            key = k.encode('utf-8').decode('unicode_escape')
                            val = v.encode('utf-8').decode('unicode_escape')
                            payload[key] = val
                        except Exception:
                            payload[k] = v
                
    except Exception as e:
        logger.error(f"КРИТИЧЕСКАЯ ОШИБКА ЧТЕНИЯ: {e}")
        return {"status": "error", "message": str(e)}
    # ------------------------------------------

    if not payload:
        logger.error("Payload пустой после всех попыток парсинга.")
        return {"status": "ok", "message": "Empty payload"}

    routes_result = await db.execute(select(Route).where(Route.form_id == form.id, Route.is_active == True))
    routes = routes_result.scalars().all()

    if not routes:
        logger.warning(f"Нет активных маршрутов для формы {form.name}")
        return {"status": "ok"}

    message_text = format_answers(payload)

    for route in routes:
        bot_result = await db.execute(select(VkBot).where(VkBot.id == route.bot_id))
        bot = bot_result.scalars().first()
        if bot:
            decrypted_token = fernet.decrypt(bot.encrypted_token.encode()).decode()
            background_tasks.add_task(send_vk_message, decrypted_token, route.target_vk_id, message_text, bot.group_id)

    return {"status": "ok"}

async def send_vk_message(token: str, peer_id: str, text: str, group_id: str):
    url = "https://api.vk.com/method/messages.send"
    params = {
        "access_token": token,
        "peer_id": peer_id,
        "message": text,
        "random_id": 0,
        "v": "5.131",
        "from_group": 1
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, params=params, timeout=10)
            logger.info(f"VK API response: {resp.json()}")
    except Exception as e:
        logger.error(f"Ошибка отправки в ВК: {e}")

# --- ПАРСЕР ЯНДЕКС.ФОРМЫ ---
QUESTIONS_MAP = {
    "answer_short_text_93740": "Имя",
    "answer_short_text_93741": "Фамилия",
    "answer_non_profile_email_93742": "Отчество",
    "answer_phone_93743": "Телефон",
    "answer_long_text_96199": "Длинный текст",
    "answer_short_text_96201": "Короткий текст",
    "answer_non_profile_email_96202": "Email",
    "answer_choices_96193": "Выбор варианта",
}

def format_answers(data):
    lines = ["📋 Новый ответ из Яндекс.Формы:\n"]

    # Если пришла строка с экранированными кавычками
    if isinstance(data, str):
        try:
            data = json.loads(data.replace('\\"', '"'))
        except (json.JSONDecodeError, TypeError):
            lines.append(data)
            return "\n".join(lines)

    # --- ФОРМАТ 1: Вложенный (answer -> data) ---
    if isinstance(data, dict) and 'answer' in data and isinstance(data['answer'], dict):
        ans = data['answer']
        created = data.get('created')
        uid = ans.get('uid')
        if created: lines.append(f"🕒 {created}")
        if uid: lines.append(f"👤 UID: {uid}")
        lines.append("")

        fields = ans.get('data') or {}
        for slug, field in fields.items():
            if isinstance(field, dict):
                value = field.get('value')
            else:
                value = field

            if isinstance(value, dict):
                value = value.get('text') or value.get('value') or str(value)
            elif isinstance(value, list):
                formatted_values = []
                for v in value:
                    if isinstance(v, dict):
                        formatted_values.append(str(v.get('text') or v.get('value') or v))
                    else:
                        formatted_values.append(str(v))
                value = ", ".join(formatted_values)

            if slug in QUESTIONS_MAP:
                name = QUESTIONS_MAP[slug]
            else:
                parts = slug.split('_')
                name = f"Вопрос {parts[-1]}" if parts[-1].isdigit() else slug

            lines.append(f"• {name}: {value}")
            
        return "\n".join(lines)

     # --- ФОРМАТ 2: Плоский JSON ---
    if isinstance(data, dict):
        ignored_keys = ['id', 'survey_id', 'created', 'lang', 'answer', 'started', 'uid']
        found_any = False
        
        for key, value in data.items():
            if key in ignored_keys:
                continue
            
            if isinstance(value, dict):
                value = value.get('text') or value.get('value') or str(value)
            elif isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            
            # ГЛАВНЫЙ ФИКС: Просто берем ключ. 
            # Python автоматически превратит \u0424\u0430... в нормальную "Ф"
            display_key = key 
            
            lines.append(f"• {display_key}: {value}")
            found_any = True
            
        if found_any:
            return "\n".join(lines)
        else:
            lines.append("⚠️ Не удалось распознать поля в ответе.")
            lines.append(json.dumps(data, ensure_ascii=False, indent=2)[:500])
            return "\n".join(lines)

    # --- ФОРМАТ 3: Резервный ---
    lines.append(str(data))
    return "\n".join(lines)