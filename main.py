# file: main.py
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import asyncpg
import os
from dotenv import load_dotenv

load_dotenv()  # loads from .env file
DATABASE_URL = os.getenv("SUPABASE_DB_URL")

app = FastAPI(
    title="Tracer Study SMA API",
    version="1.0.0",
    description="Dokumentasi API untuk tracer study alumni SMA"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def get_db():
    return await asyncpg.connect(DATABASE_URL)

# Models
class AlumniCheckRequest(BaseModel):
    nisn: str
    nis: str
    nik: str
    tanggal_lahir: str

class TracerData(BaseModel):
    id_alumni: int
    alamat_email: str
    no_telepon: str
    status: str
    perguruan_tinggi: str
    program_studi: str
    sumber_biaya: str
    tahun_masuk: int
    jawaban_kuesioner: dict

class AlumniCreate(BaseModel):
    nisn: str
    nis: str
    nik: str
    nama_siswa: str
    tanggal_lahir: str
    tahun_lulus: int

class LoginRequest(BaseModel):
    email: str = Form(...)
    password: str = Form(...)

# 1. Check alumni
@app.post("/alumni/check")
async def check_alumni(data: AlumniCheckRequest):
    conn = await get_db()
    result = await conn.fetchrow("""
        SELECT id_alumni, nama_siswa, tahun_lulus FROM alumni
        WHERE nisn=$1 AND nis=$2 AND nik=$3 AND tanggal_lahir=$4
    """, data.nisn, data.nis, data.nik, data.tanggal_lahir)
    await conn.close()
    if result:
        return dict(result)
    raise HTTPException(status_code=404, detail="Alumni not found")

# 2. Submit tracer study
@app.post("/tracer/submit")
async def submit_tracer(data: TracerData, bukti_kuliah: UploadFile = File(...)):
    conn = await get_db()
    status_id = await conn.fetchval("SELECT kode_status FROM status WHERE status=$1", data.status)
    pt_id = await conn.fetchval("SELECT id_perguruan_tinggi FROM perguruan_tinggi WHERE perguruan_tinggi=$1", data.perguruan_tinggi)
    ps_id = await conn.fetchval("SELECT id_program_studi FROM program_studi WHERE nama_program_studi=$1", data.program_studi)
    sumber_id = await conn.fetchval("SELECT id_sumber_biaya FROM sumber_biaya WHERE sumber_biaya=$1", data.sumber_biaya)
    tracer_id = await conn.fetchval("""
        INSERT INTO tracer(id_alumni, kode_status, is_filled, fill_date)
        VALUES($1, $2, true, CURRENT_DATE)
        RETURNING id_tracer
    """, data.id_alumni, status_id)

    contents = await bukti_kuliah.read()
    await conn.execute("""
        INSERT INTO detail_pendidikan_tinggi(id_tracer, id_perguruan_tinggi, id_program_studi, tahun_masuk, id_sumber_biaya, bukti_kuliah)
        VALUES($1, $2, $3, $4, $5, $6)
    """, tracer_id, pt_id, ps_id, data.tahun_masuk, sumber_id, contents)

    for q_name, a_text in data.jawaban_kuesioner.items():
        q_id = await conn.fetchval("SELECT id_kuesioner FROM kuesioner WHERE pertanyaan=$1", q_name)
        a_id = await conn.fetchval("SELECT id_jawaban FROM jawaban WHERE jawaban=$1", a_text)
        await conn.execute("""
            INSERT INTO detail_kuesioner(id_tracer, id_kuesioner, id_jawaban)
            VALUES($1, $2, $3)
        """, tracer_id, q_id, a_id)

    await conn.close()
    return {"message": "Tracer data submitted successfully"}

# 3. Get Perguruan Tinggi dan Program Studi
# 3. Get Perguruan Tinggi dan Program Studi
@app.get("/referensi/perguruan-tinggi")
async def get_pt_prodi():
    conn = await get_db()
    rows = await conn.fetch("""
        SELECT pt.id_perguruan_tinggi, pt.perguruan_tinggi, ps.id_program_studi, ps.nama_program_studi
        FROM perguruan_tinggi_prodi pp
        JOIN perguruan_tinggi pt ON pt.id_perguruan_tinggi = pp.id_perguruan_tinggi
        JOIN program_studi ps ON ps.id_program_studi = pp.id_program_studi
    """)
    await conn.close()

    data = {}
    for row in rows:
        pt_id = row["id_perguruan_tinggi"]
        if pt_id not in data:
            data[pt_id] = {
                "id_perguruan_tinggi": pt_id,
                "perguruan_tinggi": row["perguruan_tinggi"],
                "program_studi": []
            }
        data[pt_id]["program_studi"].append({
            "id_program_studi": row["id_program_studi"],
            "program_studi": row["nama_program_studi"]
        })

    return list(data.values())

# 4. Get Kuesioner & Jawaban
@app.get("/referensi/kuesioner")
async def get_kuesioner():
    conn = await get_db()
    q = await conn.fetch("SELECT * FROM kuesioner")
    a = await conn.fetch("SELECT * FROM jawaban")
    await conn.close()
    return {"pertanyaan": [dict(row) for row in q], "jawaban": [dict(row) for row in a]}

# 5. Get Status
@app.get("/referensi/status")
async def get_status():
    conn = await get_db()
    rows = await conn.fetch("SELECT * FROM status")
    await conn.close()
    return [dict(row) for row in rows]

# 6. Statistik alumni per tahun
@app.get("/statistik/alumni")
async def statistik_alumni():
    conn = await get_db()
    result = await conn.fetch("""
        SELECT tahun_lulus,
               COUNT(*) AS jumlah_alumni,
               SUM(CASE WHEN is_filled THEN 1 ELSE 0 END) AS alumni_mengisi,
               SUM(CASE WHEN kode_status = 'MELANJUTKAN' THEN 1 ELSE 0 END) AS lanjut_pendidikan
        FROM alumni a
        LEFT JOIN tracer t ON a.id_alumni = t.id_alumni
        GROUP BY tahun_lulus
        ORDER BY tahun_lulus
    """)
    await conn.close()
    return [dict(row) for row in result]

# 7. Statistik jawaban kuesioner per tahun
@app.get("/statistik/kuesioner")
async def statistik_kuesioner():
    conn = await get_db()
    result = await conn.fetch("""
        SELECT t.fill_date, k.pertanyaan, j.jawaban, COUNT(*) AS jumlah
        FROM detail_kuesioner dk
        JOIN tracer t ON dk.id_tracer = t.id_tracer
        JOIN kuesioner k ON dk.id_kuesioner = k.id_kuesioner
        JOIN jawaban j ON dk.id_jawaban = j.id_jawaban
        GROUP BY t.fill_date, k.pertanyaan, j.jawaban
        ORDER BY t.fill_date
    """)
    await conn.close()
    return [dict(row) for row in result]

# 8. Tambah alumni
@app.post("/alumni/create")
async def create_alumni(data: AlumniCreate):
    conn = await get_db()
    await conn.execute("""
        INSERT INTO alumni(nisn, nis, nik, nama_siswa, tanggal_lahir, tahun_lulus)
        VALUES($1, $2, $3, $4, $5, $6)
    """, data.nisn, data.nis, data.nik, data.nama_siswa, data.tanggal_lahir, data.tahun_lulus)
    await conn.close()
    return {"message": "Alumni created successfully"}

# 9. Detail alumni lengkap
@app.get("/alumni/{id_alumni}")
async def detail_alumni(id_alumni: int):
    conn = await get_db()
    result = await conn.fetchrow("""
        SELECT a.*, t.is_filled, t.kode_status, s.status,
               dpt.tahun_masuk, dpt.bukti_kuliah,
               pt.perguruan_tinggi, ps.nama_program_studi, sb.sumber_biaya
        FROM alumni a
        LEFT JOIN tracer t ON t.id_alumni = a.id_alumni
        LEFT JOIN status s ON s.kode_status = t.kode_status
        LEFT JOIN detail_pendidikan_tinggi dpt ON dpt.id_tracer = t.id_tracer
        LEFT JOIN perguruan_tinggi pt ON pt.id_perguruan_tinggi = dpt.id_perguruan_tinggi
        LEFT JOIN program_studi ps ON ps.id_program_studi = dpt.id_program_studi
        LEFT JOIN sumber_biaya sb ON sb.id_sumber_biaya = dpt.id_sumber_biaya
        WHERE a.id_alumni = $1
    """, id_alumni)

    kuesioner = await conn.fetch("""
        SELECT k.pertanyaan, j.jawaban
        FROM detail_kuesioner dk
        JOIN kuesioner k ON k.id_kuesioner = dk.id_kuesioner
        JOIN jawaban j ON j.id_jawaban = dk.id_jawaban
        WHERE dk.id_tracer = (SELECT id_tracer FROM tracer WHERE id_alumni=$1)
    """, id_alumni)

    await conn.close()
    return {"alumni": dict(result), "kuesioner": [dict(k) for k in kuesioner]}

# 10. Login
@app.post("/login")
async def login(data: LoginRequest):
    conn = await get_db()
    result = await conn.fetchrow('SELECT nama FROM "user" WHERE username=$1 AND password=$2', data.email, data.password)
    await conn.close()
    if not result:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"message": "Login successful", "data": dict(result)}
