import os
import json
import logging
import random
from flask import Flask, request, jsonify
import vk_api
from vk_api.exceptions import ApiError

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Загрузка переменных окружения
VK_TOKEN = os.environ.get('VK_TOKEN')
VK_GROUP_ID = os.environ.get('VK_GROUP_ID')
RECIPIENT_IDS = os.environ.get('RECIPIENT_IDS', '').split(',')  # список ID через запятую

if not VK_TOKEN:
    raise ValueError("Не задан VK_TOKEN")
if not RECIPIENT_IDS or RECIPIENT_IDS == ['']:
    raise ValueError("Не задан RECIPIENT_IDS")

# Инициализация VK API от имени сообщества
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()

def send_vk_message(peer_id, text):
    """Отправка сообщения от имени сообщества"""
    try:
        vk.messages.send(
            peer_id=int(peer_id),
            message=text,
            random_id=random.randint(-2147483648, 2147483647),
            from_group=1  # ключевой параметр: отправка от имени сообщества
        )
        logger.info(f"Сообщение отправлено пользователю {peer_id}")
    except ApiError as e:
        logger.error(f"Ошибка VK API при отправке {peer_id}: {e}")

# Словарь соответствий: техническое имя поля → человеческое название
# Значения slug вы можете найти в логах (см. "answer_short_text_93740" и т.д.)
QUESTIONS_MAP = {
    "answer_short_text_93740":      "Имя",
    "answer_short_text_93741":      "Отчество",
    "answer_non_profile_email_93742": "Фамилия",
    "answer_phone_93743":           "Телефон",
    "answer_short_text_9008991131733664":           "Ссылка ВК",
}


def _deep_parse(data):
    """Разбирает строку в JSON до тех пор, пока не получится объект."""
    for _ in range(3):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                break
        else:
            break
    return data


def format_answers(data):
    """Преобразование ответов Яндекс.Формы в читаемый текст."""
    data = _deep_parse(data)
    lines = ["📋 Новый ответ из Яндекс.Формы:\n"]

    # === Формат Яндекс.Формы (JSON-RPC) ===
    if isinstance(data, dict) and 'answer' in data and isinstance(data['answer'], dict):
        ans = data['answer']

        created = data.get('created')
        uid = ans.get('uid')
        if created:
            lines.append(f"🕒 {created}")
        if uid:
            lines.append(f"👤 UID: {uid}")
        lines.append("")

        fields = ans.get('data') or {}
        for slug, field in fields.items():
            if isinstance(field, dict):
                value = field.get('value')
            else:
                value = field

            # Если значение — список (чекбоксы, множественный выбор)
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)

            name = QUESTIONS_MAP.get(slug, slug)
            lines.append(f"• {name}: {value}")

        return "\n".join(lines)

    # === Резервные форматы (на случай изменений) ===
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

    if isinstance(data, dict) and 'answers' in data:
        parse_item(data['answers'])
    else:
        parse_item(data)

    return "\n".join(lines)

@app.route('/webhook/yandex', methods=['POST'])
def yandex_webhook():
    """Приём вебхука от Яндекс.Формы"""
    try:
        payload = None

        # 1. Если пришёл JSON (Content-Type: application/json)
        if request.is_json:
            payload = request.get_json(silent=True)

        # 2. Если пришёл form-data
        if not payload and request.form:
            form_data = request.form.to_dict()
            if 'answers' in form_data:
                try:
                    payload = json.loads(form_data['answers'])
                except (json.JSONDecodeError, TypeError):
                    payload = form_data
            else:
                payload = form_data

        # 3. Читаем сырое тело (основной случай для Яндекс.Формы)
        if not payload:
            raw = request.get_data(as_text=True)
            logger.info(f"Сырое тело запроса: {raw[:1000]}")
            if raw:
                # Попытка 1: как есть
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    # Попытка 2: убрать экранирование кавычек (Яндекс.Форма)
                    try:
                        payload = json.loads(raw.replace('\\"', '"'))
                    except json.JSONDecodeError:
                        payload = raw

        if not payload:
            payload = "(пустой запрос)"

        logger.info(
            "Получен вебхук: " + (
                json.dumps(payload, ensure_ascii=False)[:500]
                if not isinstance(payload, str) else payload[:500]
            )
        )

        message_text = format_answers(payload)

        for recipient in RECIPIENT_IDS:
            recipient = recipient.strip()
            if recipient:
                send_vk_message(recipient, message_text)

        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.exception("Ошибка при обработке вебхука")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)