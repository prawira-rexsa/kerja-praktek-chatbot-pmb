import os
from flask import Flask, request, jsonify
from dotenv import load_dotenv

from sqlalchemy import create_engine
from langchain_ollama import ChatOllama
from langchain_community.utilities import SQLDatabase
from langchain.chains import create_sql_query_chain
from langchain_community.tools.sql_database.tool import QuerySQLDataBaseTool
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

load_dotenv()

DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "daft_online")

connection_string = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"
db = SQLDatabase.from_uri(connection_string)

def load_instructions(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()

INTRUKSI_ASISTEN = load_instructions("instruksi.txt")

llm = ChatOllama(model="minimax-m3:cloud", temperature=0)

write_query = create_sql_query_chain(llm, db)

execute_query = QuerySQLDataBaseTool(db=db)

answer_prompt = PromptTemplate.from_template(
    """Anda adalah Asisten Virtual PMB ITATS.
    
    Riwayat Percakapan Sebelumnya:
    {chat_history}
    
    Berikut adalah instruksi gaya bahasa dan aturan Anda:
    {instruksi}
    
    Berdasarkan pertanyaan user, query SQL yang dijalankan, dan hasil dari database, buatlah jawaban akhir yang rapi dan natural sesuai instruksi di atas.
    ATURAN PALING KETAT: HANYA tuliskan item dan nominal angka yang SECARA EKSPLISIT ada di dalam "SQL Result". JANGAN PERNAH menambahkan item biaya (seperti Daftar Ulang, dll) jika item tersebut tidak ada di dalam SQL Result. Jika Anda melanggar ini, Anda berhalusinasi.
    
    Pertanyaan User: {question}
    SQL Query: {query}
    SQL Result: {result}
    Jawaban: """
)

answer_chain = answer_prompt | llm | StrOutputParser()

app = Flask(__name__)

# Dictionary untuk menyimpan riwayat per nomor WA (In-Memory)
user_histories = {}

@app.route("/api/chat", methods=["POST"])
def chat_api():
    """Endpoint untuk menerima pesan masuk dari Node.js (bot.js)"""
    data = request.get_json()
    
    if not data or "message" not in data:
        return jsonify({"error": "Bad Request"}), 400
        
    user_question = data["message"]
    sender_phone = data.get("sender", "Unknown")
    
    # Ambil riwayat percakapan untuk nomor ini
    history_list = user_histories.get(sender_phone, [])
    chat_history_str = "\n".join(history_list) if history_list else "(Belum ada riwayat)"
    
    print(f"\n[+] Memproses pesan dari {sender_phone.replace('@c.us', '')}: {user_question}")
    
    sapaan = ["p", "ping", "halo", "hai", "hi", "test", "tes"]
    if user_question.lower().strip() in sapaan:
        bot_reply = "Halo Kak! 👋 Saya Asisten Virtual PMB ITATS. Ada yang bisa saya bantu terkait info pendaftaran, biaya kuliah, atau jurusan?"
        return jsonify({"reply": bot_reply}), 200
    
    try:
        contextual_question = f"""Riwayat Percakapan Sebelumnya:
{chat_history_str}

Pertanyaan Baru: {user_question}

Catatan Penting: Harus difilter untuk tahun 2025/2026. ITATS HANYA punya jenjang S1, S2, RPL. 
ATURAN BIAYA AWAL: Jika user bertanya 'Biaya Awal' atau 'Budget', Anda WAJIB membuat SQL seperti contoh ini:
SELECT 
  (SELECT nilai FROM biaya_pendaftaran WHERE detail='biaya_daftar') AS biaya_daftar,
  (SELECT jumlah FROM biaya_kuliah WHERE jenis_biaya='Biaya Daftar Ulang') AS daftar_ulang,
  (SELECT jumlah FROM biaya_kuliah WHERE jenis_biaya='SPP Pagi') AS spp,
  (SELECT dana_pembangunan FROM pmb_jurusan WHERE nama_jurusan='Teknik Mesin') AS dana_pembangunan;
Modifikasi 'SPP Pagi/Malam' dan 'nama_jurusan' pada contoh di atas sesuai pertanyaan user!"""
        
        print("[*] Generating SQL Query...")
        sql_query = write_query.invoke({"question": contextual_question})
        
        import re
        
        markdown_match = re.search(r'```sql\s*(.*?)\s*```', sql_query, flags=re.DOTALL | re.IGNORECASE)
        if markdown_match:
            sql_query = markdown_match.group(1).strip()
        else:
            if "SQLQuery:" in sql_query:
                sql_query = sql_query.split("SQLQuery:")[-1].strip()
            
            sql_query = re.split(r'(?i)SQLResult:|Answer:|##|</', sql_query)[0].strip()

            select_idx = sql_query.upper().find("SELECT")
            if select_idx != -1:
                sql_query = sql_query[select_idx:].strip()
                
            if ";" in sql_query:
                sql_query = sql_query.split(";")[0] + ";"
                
        print(f"[*] Executing SQL: {sql_query}")
        
        sql_result = execute_query.invoke(sql_query)
        print(f"[*] SQL Result: {sql_result}")
        
        print("[*] Generating Human Answer...")
        bot_reply = answer_chain.invoke({
            "chat_history": chat_history_str,
            "instruksi": INTRUKSI_ASISTEN,
            "question": user_question,
            "query": sql_query,
            "result": sql_result
        })
        
        import re
        bot_reply = re.sub(r'<think>.*?</think>', '', bot_reply, flags=re.DOTALL).strip()
        
        bot_reply = bot_reply.replace("Jawaban:", "").strip()
        
        # --- Simpan ke Memori ---
        if sender_phone not in user_histories:
            user_histories[sender_phone] = []
        
        user_histories[sender_phone].append(f"User: {user_question}")
        # Potong reply jika terlalu panjang agar tidak membebani token LLM di chat berikutnya
        short_reply = bot_reply[:300] + "..." if len(bot_reply) > 300 else bot_reply
        user_histories[sender_phone].append(f"Bot: {short_reply}")
        
        # Simpan maksimal 6 baris terakhir (3 pasang tanya-jawab terakhir)
        if len(user_histories[sender_phone]) > 6:
            user_histories[sender_phone] = user_histories[sender_phone][-6:]
        # ------------------------
        
    except Exception as e:
        print(f"[-] Error dari LangChain: {e}")
        bot_reply = "Waduh Kak, sepertinya sistem lagi sibuk. Bisa coba tanya lebih spesifik? (Contoh: 'Biaya kelas malam')"
            
    return jsonify({"reply": bot_reply}), 200

if __name__ == "__main__":
    print("="*50)
    print("SERVER API ASISTEN PMB ITATS AKTIF (PORT 5000)")
    print("="*50)
    app.run(port=5000, debug=False)
