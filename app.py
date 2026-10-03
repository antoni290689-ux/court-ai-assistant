import streamlit as st
from pypdf import PdfReader
import re
import fitz
import pytesseract
from PIL import Image
import io
import os
import json
import sqlite3
import pandas as pd
from io import BytesIO
from dotenv import load_dotenv
from groq import Groq
import smtplib
from email.message import EmailMessage
load_dotenv()



if os.name == "nt":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


st.set_page_config(
    page_title="ИИ-помощник по судебным документам",
    page_icon="⚖️",
    layout="wide"
)
# Хранилище проанализированных документов
if "registry" not in st.session_state:
    st.session_state.registry = []

def init_db():
    conn = sqlite3.connect("registry.db")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT,
            document_type TEXT,
            case_number TEXT,
            court TEXT,
            plaintiff TEXT,
            defendant TEXT,
            document_date TEXT,
            principal_debt TEXT,
            penalty TEXT,
            state_duty TEXT,
            total TEXT
        )
    """)

    conn.commit()
    conn.close()
    return True


init_db()
def save_to_db(filename, analysis):
    conn = sqlite3.connect("registry.db")
    cursor = conn.cursor()
    # Проверяем, есть ли уже такой файл в реестре
    cursor.execute(
        "SELECT id FROM documents WHERE filename = ?",
        (filename,)
    )

    if cursor.fetchone():
        conn.close()
        return False
    cursor.execute("""
        INSERT INTO documents (
            filename,
            document_type,
            case_number,
            court,
            plaintiff,
            defendant,
            document_date,
            principal_debt,
            penalty,
            state_duty,
            total
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        filename,
        analysis.get("Тип документа", "Не найдено"),
        analysis.get("Номер дела", "Не найдено"),
        analysis.get("Суд", "Не найдено"),
        analysis.get("Истец", "Не найдено"),
        analysis.get("Ответчик", "Не найдено"),
        analysis.get("Дата документа", "Не найдено"),
        analysis.get("Основной долг", "Не найдено"),
        analysis.get("Пени", "Не найдено"),
        analysis.get("Госпошлина", "Не найдено"),
        analysis.get("Итого", "Не найдено")
    ))

    conn.commit()
    conn.close()
def load_from_db():
    conn = sqlite3.connect("registry.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM documents ORDER BY id DESC")
    rows = cursor.fetchall()

    conn.close()

    return [dict(row) for row in rows]


def delete_from_db(record_id):
    conn = sqlite3.connect("registry.db")
    cursor = conn.cursor()

    cursor.execute(
        "DELETE FROM documents WHERE id = ?",
        (record_id,)
    )

    conn.commit()
    conn.close() 
def extract_text_from_pdf(file):
    reader = PdfReader(file)
    text = ""

    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text += page_text + "\n"

    return text
def extract_text_with_ocr(file):
    file.seek(0)
    pdf_bytes = file.read()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    text = ""

    for page in doc:
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        page_text = pytesseract.image_to_string(img, lang="rus+eng")
        text += page_text + "\n"

    return text
def is_text_quality_good(text):
    if not text or len(text.strip()) < 100:
        return False

    suspicious_words = [
        "ESRI",
        "Не найден",
    ]

    for word in suspicious_words:
        if word.lower() in text.lower():
            return False

    return True    
def analyze_document_ai(text):
    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        raise ValueError("GROQ_API_KEY не найден")

    client = Groq(api_key=api_key)

    prompt = f"""
Ты анализируешь судебный документ для формирования реестра.

Извлекай ТОЛЬКО сведения, которые явно присутствуют в тексте.
Не придумывай данные, не исправляй OCR по догадке и не выполняй
арифметические вычисления над денежными суммами.

ВАЖНО:
1. Тип документа определи как один из:
   "Судебный приказ", "Исполнительный лист",
   "Постановление ФССП" или "Не найдено".
2. Номер дела указывай только отдельно от даты.
   Дату нельзя включать в номер дела.
   Если OCR-строка выглядит сомнительно и достоверный номер
   определить нельзя — укажи "Не найдено".
3. "Номер ИП" — номер исполнительного производства.
    Номера, оканчивающиеся на "-ИП", например 835049/33/55003-ИП, записывай только в поле "Номер ИП".
    Такой номер нельзя использовать как "Номер дела".
    Если номер дела отдельно не указан — "Не найдено".  
4. "Основной долг" — только сумма задолженности, прямо указанная
   в документе.
5. "Пени" — только явно указанная сумма пеней.
6. "Госпошлина" — только явно указанная государственная пошлина.
7. "Итого" заполняй ТОЛЬКО если итоговая сумма прямо написана
   в документе. Самостоятельно суммы не складывай.
8. Если значение достоверно определить нельзя — "Не найдено".
    8. "Дата документа" — укажи дату документа в формате ДД.ММ.ГГГГ.
       Если рядом с заголовком документа или номером дела явно указана дата,
       используй эту дату.
       Не придумывай дату. Если достоверно определить дату нельзя — "Не найдено".
Верни только JSON следующего вида:

{{
    "Тип документа": "",
    "Номер дела": "",
    "Номер ИП": "",
    "Суд": "",
    "Истец": "",
    "Ответчик": "",
    "Дата документа": "",
    "Основной долг": "",
    "Пени": "",
    "Госпошлина": "",
    "Итого": ""
}}

Текст документа:
{text}
"""

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        response_format={"type": "json_object"}
    )

    result = json.loads(response.choices[0].message.content)

    # Проверяем номер дела после ответа ИИ
    case_number = str(result.get("Номер дела", "")).strip()

    # Настоящий номер дела должен содержать хотя бы одну цифру
    if not re.search(r"\d", case_number):
        result["Номер дела"] = "Не найдено"
        # Номер исполнительного производства
    ip_number = str(result.get("Номер ИП", "")).strip()

    # Номер ИП не должен попадать в поле "Номер дела"
    if "-ИП" in case_number.upper():
        if not ip_number:
            result["Номер ИП"] = case_number
        result["Номер дела"] = "Не найдено"    
        # Приводим денежные суммы к единому формату
    for field in ["Основной долг", "Пени", "Госпошлина", "Итого"]:
        value = str(result.get(field, "")).strip()

        if value and value != "Не найдено":
            try:
                clean_value = value.lower().replace("руб.", "").replace("руб", "")
                clean_value = clean_value.replace(" ", "").replace(",", ".")
                number = float(clean_value)

                result[field] = f"{number:,.2f}".replace(",", " ").replace(".", ",") + " руб."
            except ValueError:
                pass
    return result

def needs_lawyer_review(text):
    """
    Определяет, требуется ли дополнительная проверка документа юристом.
    """

    if not text:
        return False, []

    text_lower = text.lower()

    markers = [
        "отменить судебный приказ",
        "возражения относительно",
        "восстановить срок",
        "поворот исполнения",
        "обжалован",
        "оспорен"
    ]

    found_markers = [
        marker for marker in markers
        if marker in text_lower
    ]

    return len(found_markers) > 0, found_markers


def send_document_email(recipient_email, filename, analysis, text):
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "465"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")

    if not smtp_server or not smtp_user or not smtp_password:
        raise ValueError("Не настроены SMTP-параметры в .env")

    msg = EmailMessage()

    msg["Subject"] = f"Судебный документ требует проверки: {filename}"
    msg["From"] = smtp_user
    msg["To"] = recipient_email

    body = f"""
Документ требует дополнительной проверки юристом.

Файл: {filename}
Тип документа: {analysis.get("Тип документа", "Не найдено")}
Номер дела: {analysis.get("Номер дела", "Не найдено")}
Номер ИП: {analysis.get("Номер ИП", "Не найдено")}
Суд: {analysis.get("Суд", "Не найдено")}
Ответчик: {analysis.get("Ответчик", "Не найдено")}

Причина проверки:
{analysis.get("Причина проверки", "Не указана")}

Извлечённый текст документа:

{text}
"""

    msg.set_content(body)

    with smtplib.SMTP_SSL(smtp_server, smtp_port) as server:
        server.login(smtp_user, smtp_password)
        server.send_message(msg)

def extract_text_from_xml(file):
    file.seek(0)

    try:
        xml_bytes = file.read()

        # Пробуем основные кодировки
        try:
            text = xml_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = xml_bytes.decode("windows-1251")
            except UnicodeDecodeError:
                text = xml_bytes.decode("utf-8", errors="ignore")

        return text

    except Exception as e:
        return f"Ошибка чтения XML: {e}"
def analyze_fssp_xml(file):
    import xml.etree.ElementTree as ET

    file.seek(0)
    xml_bytes = file.read()

    root = ET.fromstring(xml_bytes)

    # Namespace ФССП
    ns = {"fssp": "http://www.fssprus.ru/namespace/order/2017/2"}

    def get_value(tag):
        element = root.find(f"fssp:{tag}", ns)

        if element is not None and element.text:
            return element.text.strip()

        return "Не найдено"

    result = {
        "Тип документа": get_value("DocName"),
        "Номер дела": get_value("IdDeloNo"),
        "Суд": get_value("IdCourtName"),
        "Истец": get_value("IdCrdrName"),
        "Ответчик": get_value("DbtrName"),
        "Дата документа": get_value("DocDate"),
        "Основной долг": get_value("IdDebtSum"),
        "Пени": "Не найдено",
        "Госпошлина": "Не найдено",
        "Итого": get_value("TotalArrestDebtSum"),
        "Номер ИП": get_value("IpNo"),
    }

    return result
def analyze_document(text):
    result = {
        "Тип документа": "Не определено",
        "Номер дела": "Не найден",
        "Суд": "Не найден",
        "Истец": "Не найден",
        "Ответчик": "Не найден",
        "Дата документа": "Не найдена",
        "Сумма требований": "Не найдена",
    }

    clean_text = " ".join(text.split())

    # Тип документа
    text_lower = clean_text.lower()

    if "исполнительный лист" in text_lower:
        result["Тип документа"] = "Исполнительный лист"
    elif "судебный приказ" in text_lower:
        result["Тип документа"] = "Судебный приказ"
    elif "решение суда" in text_lower or "решение" in text_lower:
        result["Тип документа"] = "Решение суда"
    elif "определение" in text_lower:
        result["Тип документа"] = "Определение суда"
    elif "исковое заявление" in text_lower:
        result["Тип документа"] = "Исковое заявление"

        # Номер дела
    # Берём значение только если в номере есть хотя бы одна цифра
    case_match = re.search(
        r"(?:дело|дела)\s*№?\s*([А-ЯA-ZА-Яа-я0-9\-\/]+)",
        clean_text,
        re.IGNORECASE
    )

    if case_match:
        case_number = case_match.group(1).strip()

        # ESRI и другой текст без цифр номером дела не считаем
        if re.search(r"\d", case_number):
            result["Номер дела"] = case_number
        # Номер исполнительного производства ФССП
    ip_match = re.search(
        r"(?:№\s*)?(\d{4,12})[\s/]*(\d{2})[\s/]*(\d{4,6})\s*[-–—]?\s*ИП",
        clean_text,
        re.IGNORECASE
    )

    if ip_match:
        result["Номер ИП"] = f"{ip_match.group(1)}/{ip_match.group(2)}/{ip_match.group(3)}-ИП"
    else:
        result["Номер ИП"] = "Не найден"
    # Дата документа
    date_match = re.search(
        r"\b(\d{2}[./]\d{2}[./]\d{4})\b",
        clean_text
    )

    if date_match:
        result["Дата документа"] = date_match.group(1)

        # Суд
    court_patterns = [
        r"((?:мировой судья|мирового судьи)\s+судебного участка\s*№?\s*\d+[^,.]*?(?:АО|района|г\.)[^,.]*)",
        r"((?:районный|городской|арбитражный)\s+суд[^,.]{0,150})",
    ]

    for pattern in court_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            result["Суд"] = match.group(1).strip(" ,.-")
            break

        # Истец
    plaintiff_match = re.search(
        r"по\s+иску\s+(.{3,250}?)\s+к\s+(?:рассмотрев\s*[—\-]?\s*)?",
        clean_text,
        re.IGNORECASE
    )

    if plaintiff_match:
        plaintiff = plaintiff_match.group(1).strip(" ,.-—")
        result["Истец"] = plaintiff

    # Ответчик / ответчики
    defendant_match = re.search(
        r"по\s+иску\s+.{3,250}?\s+к\s+(?:рассмотрев\s*[—\-]?\s*)?"
        r"(.{3,500}?)(?=\s+о\s+взыскании|\s+решил|\s+определил|\s+постановил|$)",
        clean_text,
        re.IGNORECASE
    )

    if defendant_match:
        defendants = defendant_match.group(1)

        # Убираем OCR-мусор в начале
        defendants = re.sub(
            r"^\s*рассмотрев\s*[—\-]?\s*",
            "",
            defendants,
            flags=re.IGNORECASE
        )

        defendants = defendants.strip(" ,.-—")
        result["Ответчик"] = defendants

        # Денежные суммы
    def find_amount(patterns):
        for pattern in patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                amount = match.group(1)
                amount = amount.replace(" ", "").replace(",", ".")
                return amount + " руб."
        return "Не найдено"

    # Основной долг
    result["Основной долг"] = find_amount([
        r"задолженн\w*.{0,100}?в\s+размере\s+([\d\s]+[.,]\d{2})\s*(?:руб|рублей)",
        r"основн\w*\s+долг.{0,50}?([\d\s]+[.,]\d{2})\s*(?:руб|рублей)"
    ])

    # Пени
    result["Пени"] = find_amount([
        r"пени.{0,80}?в\s+размере\s+([\d\s]+[.,]\d{2})\s*(?:руб|рублей|руб\.)",
        r"пени.{0,40}?([\d\s]+[.,]\d{2})\s*(?:руб|рублей|руб\.)"
    ])

    # Госпошлина
    result["Госпошлина"] = find_amount([
        r"(?:государственн\w+\s+пошлин\w*|госпошлин\w*).{0,80}?([\d\s]+[.,]\d{2})\s*(?:руб|рублей)"
    ])

    # Сумма требований
    result["Сумма требований"] = find_amount([
        r"(?:задолженн\w*|взыскать).{0,120}?в\s+размере\s+([\d\s]+[.,]\d{2})\s*(?:руб|рублей)",
        r"([\d\s]+[.,]\d{2})\s*(?:руб|рублей)"
    ])

    # Итого
    amounts_for_total = []

    for field in ["Основной долг", "Пени", "Госпошлина"]:
        value = result[field]

        if value != "Не найдено":
            try:
                number = float(value.replace(" руб.", ""))
                amounts_for_total.append(number)
            except ValueError:
                pass

    if amounts_for_total:
        result["Итого"] = f"{sum(amounts_for_total):.2f} руб."
    else:
        result["Итого"] = "Не найдено"
    print("RESULT:", result)
    return result
st.title("⚖️ ИИ-помощник по судебным документам")
st.write("OCR • Анализ • Извлечение данных • Формирование реестра")

with st.sidebar:
    st.header("Меню")
    page = st.radio(
        "Раздел",
        ["Загрузка документов", "Реестр документов", "Настройки"]
    )

if page == "Загрузка документов":
    st.header("Загрузка документов")

    uploaded_files = st.file_uploader(
        "Перетащите судебные документы сюда",
        type=["pdf", "xml", "png", "jpg", "jpeg"],
        accept_multiple_files=True
    )

    if uploaded_files:
        st.success(f"Загружено документов: {len(uploaded_files)}")

    # Нажимаем анализ
        if st.button("🔍 Начать анализ", type="primary"):

            st.session_state["results"] = []

            for file in uploaded_files or []:

                text = ""
                analysis = None

                if file.name.lower().endswith(".pdf"):
                    text = extract_text_from_pdf(file)

                    if not is_text_quality_good(text):
                        text = extract_text_with_ocr(file)

                    if text.strip():
                        try:
                            analysis = analyze_document_ai(text)
                        except Exception:
                            analysis = analyze_document(text)
                            st.warning(
                                "ИИ-модель недоступна. Выполнен локальный анализ документа."
                            )

                elif file.name.lower().endswith(".xml"):
                    text = extract_text_from_xml(file)

                    # XML ФССП разбираем напрямую по тегам
                    analysis = analyze_fssp_xml(file)

                elif file.name.lower().endswith((".png", ".jpg", ".jpeg")):
                    file.seek(0)
                    image = Image.open(file)

                    text = pytesseract.image_to_string(
                        image,
                        lang="rus"
                    )

                    if text.strip():
                        try:
                            analysis = analyze_document_ai(text)
                        except Exception:
                            analysis = analyze_document(text)
                            st.warning(
                                "ИИ-модель недоступна. Выполнен локальный анализ изображения."
                            )

                if analysis:
                    needs_review, review_reasons = needs_lawyer_review(text)

                    if needs_review:
                        analysis["Требуется проверка юристом"] = "Да"
                        analysis["Причина проверки"] = ", ".join(review_reasons)
                    else:
                        analysis["Требуется проверка юристом"] = "Нет"
                        analysis["Причина проверки"] = ""

                    st.session_state["results"].append({
                        "Файл": file.name,
                        "Текст": text,
                        "Анализ": analysis
                    })
            # Добавить все результаты в реестр
    if st.session_state.get("results"):
        if st.button("➕ Добавить все в реестр"):
            added_count = 0
            skipped_count = 0

            for item in st.session_state["results"]:
                record = item["Анализ"].copy()
                record["Файл"] = item["Файл"]

                added = save_to_db(item["Файл"], item["Анализ"])

                if added:
                    st.session_state.registry.append(record)
                    added_count += 1
                else:
                    skipped_count += 1

            if added_count > 0:
                st.success(f"Добавлено новых документов: {added_count}")

            if skipped_count > 0:
                st.info(f"Пропущено дубликатов: {skipped_count}")
    # Показываем сохраненные результаты анализа
    if st.session_state.get("results"):

        for item in st.session_state["results"]:

            st.subheader(item["Файл"])

            st.text_area(
                "Извлечённый текст",
                item["Текст"],
                height=400,
                key=f"text_{item['Файл']}"
            )

            st.subheader("Результат анализа")
            if item["Анализ"].get("Требуется проверка юристом") == "Да":
                st.error(
                    "⚠️ Требуется дополнительная проверка юристом"
                )

                reasons = item["Анализ"].get("Причина проверки", "")
                if reasons:
                    st.caption(f"Причина: {reasons}")
                    recipient_email = st.text_input(
                    "Email ответственного сотрудника",
                    key=f"email_{item['Файл']}"
                )

                if st.button(
                    "📧 Отправить ответственному",
                    key=f"send_{item['Файл']}"
                ):
                    if not recipient_email:
                        st.warning("Укажите email получателя.")
                    else:
                        try:
                            send_document_email(
                                recipient_email,
                                item["Файл"],
                                item["Анализ"],
                                item["Текст"]
                            )
                            st.success("Письмо успешно отправлено.")
                        except Exception as e:
                            st.error(f"Ошибка отправки письма: {e}")

            for key, value in item["Анализ"].items():
                st.write(f"**{key}:** {value}")

            if st.button(
                "➕ Добавить в реестр",
                key=f"add_{item['Файл']}"
            ):
                record = item["Анализ"].copy()
                record["Файл"] = item["Файл"]

                added = save_to_db(item["Файл"], item["Анализ"])

                if added:
                    st.session_state.registry.append(record)
                    st.success("Документ сохранён в реестр")
                else:
                    st.info("Этот документ уже есть в реестре")

    if st.button("🔄 Новый анализ"):
        st.session_state["results"] = []
        st.rerun()
elif page == "Реестр документов":
    st.header("Реестр документов")
    db_records = load_from_db()
    if db_records:
        st.success(
            f"📄 В реестре документов: {len(db_records)}"
        )

        df = pd.DataFrame(db_records)

        df = df.rename(columns={
            "id": "ID",
            "filename": "Файл",
            "document_type": "Тип документа",
            "case_number": "Номер дела",
            "court": "Суд",
            "plaintiff": "Истец",
            "defendant": "Ответчик",
            "document_date": "Дата документа",
            "principal_debt": "Основной долг",
            "penalty": "Пени",
            "state_duty": "Госпошлина",
            "total": "Итого"
        })

        st.dataframe(
            df,
            width="stretch"
        )

        excel_buffer = BytesIO()

        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            df.to_excel(
                writer,
                index=False,
                sheet_name="Реестр"
            )

        st.download_button(
            label="📥 Скачать реестр Excel",
            data=excel_buffer.getvalue(),
            file_name="reestr_sudebnyh_dokumentov.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        st.divider()
        st.subheader("Удаление документа")

        record_to_delete = st.selectbox(
            "Выберите документ для удаления",
            db_records,
            format_func=lambda x: f'ID {x["id"]} — {x["filename"]}'
        )

        if st.button("🗑️ Удалить из реестра", type="secondary"):
            delete_from_db(record_to_delete["id"])
            st.success("Документ удалён из реестра")
            st.rerun()
    else:
        st.info(
            "Реестр пока пуст. Добавьте проанализированный документ."
        )


elif page == "Настройки":
    st.header("Настройки")

    st.selectbox(
        "Языковая модель",
        [
            "GPT-OSS-120B",
            "Локальный режим (без LLM)"
        ]
    )

    st.caption(
        "На текущем этапе анализ выполняется локально. "
        "Подключение GPT-OSS-120B будет добавлено отдельно."
    )