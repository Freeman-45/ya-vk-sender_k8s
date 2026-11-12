from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from jose import JWTError, jwt
from passlib.context import CryptContext
from cryptography.fernet import Fernet
import httpx
import os
import json
import logging

from app.models import Base, User, YandexForm, VkBot, Route

# --- КОНФИГУРАЦИЯ ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://saas_user:saas_password@localhost:5432/saas_db")
SECRET_KEY = os.getenv("SECRET_KEY", "super_secret_jwt_key")
ALGORITHM = "HS256"
ALLOW_REGISTRATION = os.getenv("ALLOW_REGISTRATION", "false").lower() == "true"
# Ключ для шифрования токенов ВК (генерируется один раз и хранится в .env)
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "8jR9uZ0LqOqA_8G5_G5_G5_G5_G5_G5_G5_G5_G5_G4=")
fernet = Fernet(ENCRYPTION_KEY.encode())

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
app = FastAPI(title="Yandex-VK SaaS Gateway")

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# --- БАЗА ДАННЫХ ---
async def get_db():
    async with async_session() as session:
        yield session

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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
    
    new_user = User(email=email, hashed_password=get_password_hash(password))
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

# --- СОЗДАНИЕ БОТА (С ШИФРОВАНИЕМ) ---
@app.post("/bots")
async def create_bot(
    name: str, group_id: str, vk_token: str, 
    user: User = Depends(get_current_user), 
    db: AsyncSession = Depends(get_db)
):
    encrypted = fernet.encrypt(vk_token.encode()).decode()
    bot = VkBot(name=name, group_id=group_id, encrypted_token=encrypted, owner_id=user.id)
    db.add(bot)
    await db.commit()
    return {"id": bot.id, "name": bot.name}

# --- ОБРАБОТКА ВЕБХУКА ОТ ЯНДЕКСА ---
@app.post("/webhook/{form_id}")
async def yandex_webhook(form_id: int, request: Request, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    # 1. Ищем форму по ID
    result = await db.execute(select(YandexForm).where(YandexForm.id == form_id))
    form = result.scalars().first()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")

    # 2. Получаем данные из запроса
    raw_body = await request.body()
    try:
        body_str = raw_body.decode('utf-8').replace('\\"', '"')
        payload = json.loads(body_str)
    except Exception:
        payload = {}

    # 3. Находим все активные маршруты для этой формы
    routes_result = await db.execute(select(Route).where(Route.form_id == form.id, Route.is_active == True))
    routes = routes_result.scalars().all()

    if not routes:
        logger.warning(f"Нет маршрутов для формы {form.name}")
        return {"status": "ok"}

    # 4. Формируем текст сообщения
    message_text = format_answers(payload)

    # 5. Отправляем в фоне (чтобы Яндекс не ждал ответа от ВК)
    for route in routes:
        # Получаем бота и расшифровываем токен
        bot_result = await db.execute(select(VkBot).where(VkBot.id == route.bot_id))
        bot = bot_result.scalars().first()
        if bot:
            decrypted_token = fernet.decrypt(bot.encrypted_token.encode()).decode()
            background_tasks.add_task(send_vk_message, decrypted_token, route.target_vk_id, message_text, bot.group_id)

    return {"status": "ok"}

# --- ОТПРАВКА В ВК (АСИНХРОННО) ---
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

# Словарь для красивого вывода вопросов (добавьте остальные slug'и по аналогии)
QUESTIONS_MAP = {
    "answer_short_text_93740": "Имя, Фамилия, Отчество",
    "answer_short_text_93741": "Фамилия",
    "answer_non_profile_email_93742": "Отчество",
    "answer_phone_93743": "Телефон",
}

def format_answers(data):
    """Преобразование ответов Яндекс.Формы в читаемый текст."""
    lines = ["📋 Новый ответ из Яндекс.Формы:\n"]

    def parse_item(item):
        if isinstance(item, dict):
            q = item.get('question') or item.get('name') or item.get('title')
            a = item.get('answer') or item.get('value') or item.get('text')
            if q and a is not None:
                lines.append(f"• {q}: {a}")
            else:
                for k, v in item.items():
                    lines.append(f"• {k}: {v}")
        elif isinstance(item, list):
            for sub in item:
                parse_item(sub)
        else:
            lines.append(f"• {item}")

    if isinstance(data, str):
        try:
            data = json.loads(data.replace('\\"', '"'))
        except (json.JSONDecodeError, TypeError):
            lines.append(data)
            return "\n".join(lines)

    # Формат Яндекс.Формы (вложенный JSON)
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

            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)

            # Берем красивое имя из словаря, иначе выводим slug
            name = QUESTIONS_MAP.get(slug, slug)
            lines.append(f"• {name}: {value}")
            
        return "\n".join(lines)

    # Резервный формат (если придет что-то другое)
    if isinstance(data, dict) and 'answers' in data:
        parse_item(data['answers'])
    else:
        parse_item(data)

    return "\n".join(lines)
    return "\n".join(lines)
    
    # --- ПОЛУЧЕНИЕ СПИСКОВ ---
@app.get("/forms")
async def get_forms(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(YandexForm).where(YandexForm.owner_id == user.id))
    return result.scalars().all()

@app.post("/forms")
async def create_form(name: str, survey_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    form = YandexForm(name=name, survey_id=survey_id, owner_id=user.id)
    db.add(form)
    await db.commit()
    await db.refresh(form)
    return form

@app.get("/bots")
async def get_bots(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(VkBot).where(VkBot.owner_id == user.id))
    # Не отдаем расшифрованные токены на фронт, только названия и ID
    return [{"id": b.id, "name": b.name, "group_id": b.group_id} for b in result.scalars().all()]

@app.get("/routes")
async def get_routes(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Route).where(Route.owner_id == user.id))
    return result.scalars().all()

@app.post("/routes")
async def create_route(form_id: int, bot_id: int, target_vk_id: str, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    route = Route(form_id=form_id, bot_id=bot_id, target_vk_id=target_vk_id, owner_id=user.id)
    db.add(route)
    await db.commit()
    await db.refresh(route)
    return route