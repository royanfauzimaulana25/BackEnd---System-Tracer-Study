from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError

import asyncpg
import os
from dotenv import load_dotenv
from datetime import date
from typing import Optional, Dict, Annotated
import hypercorn
from supabase import create_client, Client
import json

load_dotenv()  # loads from .env file

# Connect Supabase via API
url : str = os.environ.get('SUPABASE_API_URL')
key : str = os.environ.get('SUPABASE_API_KEY')

DATABASE_URL = os.getenv("SUPABASE_DB_URL")

supabase: Client = create_client(url, key)
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
    return await asyncpg.connect(DATABASE_URL, statement_cache_size=0)

# Models
class AlumniCheckRequest(BaseModel):
    nisn: str
    nis: str
    nik: str
    tanggal_lahir: date

class TracerData(BaseModel):
    id_alumni: str
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
    tanggal_lahir: date
    tahun_lulus: int

class LoginRequest(BaseModel):
    email: str = Form(...)
    password: str = Form(...)

class PersonalData(BaseModel):
    alamat_email: str
    no_telepon: str

class DetailPendidikan(BaseModel):
    id_perguruan_tinggi: int
    id_program_studi: int
    tahun_masuk: int
    id_sumber_biaya: int

class SubmissionPayload(BaseModel):
    id_alumni: str
    personal_data: PersonalData
    status: str
    kuesioner: Dict[int, int]
    detail_pendidikan: Optional[DetailPendidikan] = None

# 1. Check alumni
@app.post("/alumni/check")
async def check_alumni(data: AlumniCheckRequest):
    conn = await get_db()
    result = await conn.fetchrow("""
        SELECT a.id_alumni, COALESCE(t.is_filled, false) AS is_filled
        FROM alumni a
        LEFT JOIN tracer t ON a.id_alumni = t.id_alumni
        WHERE a.nisn = $1 AND a.nis = $2 AND a.nik = $3 AND a.tanggal_lahir = $4
    """, data.nisn, data.nis, data.nik, data.tanggal_lahir)
    await conn.close()

    if result:
        return {
            "id_alumni": str(result["id_alumni"]),
            "is_filled": result["is_filled"]
        }
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
    rows = await conn.fetch("SELECT kode_status, status FROM status")
    await conn.close()
    return [dict(row) for row in rows]

# 6. Statistik alumni per tahun
@app.get("/statistik/alumni")
async def statistik_alumni():
    conn = await get_db()
    result = await conn.fetch("""
        SELECT
            COUNT(a.id_alumni) AS jumlah_siswa,
            COUNT(t.id_tracer) FILTER (WHERE t.is_filled) AS total_responden,
            COUNT(t.id_tracer) FILTER (WHERE t.kode_status = 'PEND') AS jumlah_melanjutkan
        FROM alumni a
        LEFT JOIN tracer t ON a.id_alumni = t.id_alumni
    """)
    await conn.close()

    row = result[0]
    jumlah_siswa = row["jumlah_siswa"]
    total_responden = row["total_responden"]
    jumlah_melanjutkan = row["jumlah_melanjutkan"]

    persentase_tracer = f"{round((total_responden / jumlah_siswa) * 100)}%" if jumlah_siswa else "0%"
    persentase_lanjut = f"{round((jumlah_melanjutkan / jumlah_siswa) * 100)}%" if jumlah_siswa else "0%"

    return {
        "message" : "success",
        "data": {
            "jumlahSiswa": jumlah_siswa,
            "persentaseTracerStudy": persentase_tracer,
            "totalResponden": total_responden,
            "melanjutkanPendidikan": persentase_lanjut,
            "jumlahMelanjutkanPendidikan": jumlah_melanjutkan
        }
    }

# 7. Statistik jawaban kuesioner per tahun
@app.get("/statistik/kuesioner")
async def statistik_kuesioner():
    conn = await get_db()
    result = await conn.fetch("""
        SELECT a.tahun_lulus AS tahun,
               k.pertanyaan,
               j.jawaban,
               COUNT(*) AS jumlah
        FROM detail_kuesioner dk
        JOIN tracer t ON dk.id_tracer = t.id_tracer
        JOIN alumni a ON a.id_alumni = t.id_alumni
        JOIN kuesioner k ON dk.id_kuesioner = k.id_kuesioner
        JOIN jawaban j ON dk.id_jawaban = j.id_jawaban
        GROUP BY a.tahun_lulus, k.pertanyaan, j.jawaban
        ORDER BY a.tahun_lulus
    """)
    await conn.close()

    data_map = {}
    for row in result:
        pertanyaan = row["pertanyaan"]
        tahun = str(row["tahun"])
        jawaban = row["jawaban"]
        jumlah = row["jumlah"]

        if pertanyaan not in data_map:
            data_map[pertanyaan] = {}

        if tahun not in data_map[pertanyaan]:
            data_map[pertanyaan][tahun] = {
                "tahun_lulus": tahun,
                "Sangat Bagus": 0,
                "Bagus": 0,
                "Cukup": 0,
                "Kurang": 0,
                "Sangat Kurang": 0
            }

        if jawaban in data_map[pertanyaan][tahun]:
            data_map[pertanyaan][tahun][jawaban] = jumlah
        else:
            data_map[pertanyaan][tahun][jawaban] = jumlah  # handle unexpected jawaban

    final_output = []
    for pertanyaan, tahun_data in data_map.items():
        sorted_data = sorted(tahun_data.values(), key=lambda x: x["tahun_lulus"])
        final_output.append({
            "kuesioner": pertanyaan,
            "data": sorted_data
        })

    return final_output


# 8. Tambah alumni
@app.post("/alumni/create")
async def create_alumni(data: AlumniCreate):
    conn = await get_db()
    id_alumni = await conn.fetchval("""
        INSERT INTO alumni(nisn, nis, nik, nama_siswa, tanggal_lahir, tahun_lulus)
        VALUES($1, $2, $3, $4, $5, $6) RETURNING id_alumni
    """, data.nisn, data.nis, data.nik, data.nama_siswa, data.tanggal_lahir, data.tahun_lulus)

    await conn.execute("""
        INSERT INTO tracer(id_alumni, is_filled)
        VALUES($1, FALSE)
    """, id_alumni)

    await conn.close()
    return {"message": "Alumni created successfully"}

# 9. Detail alumni lengkap
@app.get("/questionnaire/detail/{id_alumni}")
async def detail_alumni(id_alumni: str):
    conn = await get_db()
    try:
        result = await conn.fetchrow("""
            SELECT a.nisn, a.nis, a.nik, a.nama_siswa, a.tanggal_lahir, a.tahun_lulus
            FROM alumni a
            WHERE a.id_alumni = $1
        """, id_alumni)

        if result is None:
            raise HTTPException(status_code=404, detail="Alumni not found")

        return dict(result)
    finally:
        await conn.close()


# 10. Login
@app.post("/login")
async def login(data: LoginRequest):
    conn = await get_db()
    result = await conn.fetchrow('SELECT nama FROM "user" WHERE username=$1 AND password=$2', data.email, data.password)
    await conn.close()
    if not result:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"message": "Login successful", "data": dict(result)}

# 11. Get Jawaban
@app.get("/referensi/jawaban")
async def get_jawaban():
    conn = await get_db()
    rows = await conn.fetch("SELECT id_jawaban, jawaban FROM jawaban")
    await conn.close()
    return [dict(row) for row in rows]

# 12. Get full questioner metadata
@app.get("/quesioner-metadata")
async def get_questioner_metadata():
    conn = await get_db()
    perguruan_rows = await conn.fetch("""
        SELECT pt.id_perguruan_tinggi, pt.perguruan_tinggi
        FROM perguruan_tinggi pt
    """)

    status_rows = await conn.fetch("SELECT * FROM status")
    kuesioner_rows = await conn.fetch("SELECT * FROM kuesioner")
    jawaban_rows = await conn.fetch("SELECT * FROM jawaban")
    sumber_rows = await conn.fetch("SELECT * FROM sumber_biaya")
    await conn.close()

    return {
            "perguruanTinggiOptions": [dict(r) for r in perguruan_rows],
            "statusOptions": [dict(r) for r in status_rows],
            "questioner": [dict(r) for r in kuesioner_rows],
            "answerOptions": [dict(r) for r in jawaban_rows],
            "sumberBiayaOptions": [dict(r) for r in sumber_rows]
    }


# 13. Check alumni tracer status
@app.get("/tracer/status/{id_alumni}")
async def check_tracer_status(id_alumni: str):
    conn = await get_db()
    result = await conn.fetchrow("""
        SELECT t.is_filled
        FROM alumni a
        LEFT JOIN tracer t ON a.id_alumni = t.id_alumni
        WHERE a.id_alumni = $1
    """, id_alumni)
    await conn.close()

    if result:
        return result

    raise HTTPException(status_code=404, detail="Alumni not found")

# 14. Check Program Study by Perguruan Tinggi
@app.get("/programStudi/{id_perguruan_tinggi}")
async def get_program_studi(id_perguruan_tinggi: int):
    conn = await get_db()
    try:
        rows = await conn.fetch("""
            SELECT ps.id_program_studi, ps.nama_program_studi
            FROM perguruan_tinggi_prodi ptp
            JOIN program_studi ps ON ps.id_program_studi = ptp.id_program_studi
            WHERE ptp.id_perguruan_tinggi = $1
        """, id_perguruan_tinggi)

        return (dict(row) for row in rows)
    finally:
        await conn.close()


# 15. Submit kuesioner (Versi baru yang lebih baik)
@app.post("/questionnaire/submit", tags=["Tracer"])
async def submit_questionnaire(
        payload_str: Annotated[str, Form(alias="payload")],
        bukti_kuliah: Annotated[Optional[UploadFile], File()] = None,
):
    """
    Endpoint untuk menyimpan seluruh data kuesioner tracer study.

    - **payload**: String JSON yang berisi data alumni, status, kuesioner, dan detail pendidikan.
    - **bukti_kuliah**: File bukti kuliah (opsional, wajib jika status 'PEND').
    """
    try:
        # Validasi data JSON yang masuk menggunakan model Pydantic
        payload = SubmissionPayload.parse_raw(payload_str)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors())

    conn = await get_db()

    # Gunakan transaksi untuk memastikan semua data berhasil dimasukkan atau tidak sama sekali
    async with conn.transaction():
        try:
            print(payload.detail_pendidikan)
            # 1. Update data personal alumni (email dan telepon)
            await conn.execute("""
                UPDATE alumni
                SET alamat_email = $1,
                    no_telepon   = $2
                WHERE id_alumni = $3
            """, payload.personal_data.alamat_email, payload.personal_data.no_telepon, payload.id_alumni)

            # 2. Update tracer dan dapatkan id_tracer
            tracer_id = await conn.fetchval("""
                UPDATE tracer
                SET kode_status = $1,
                    is_filled   = TRUE,
                    fill_date   = CURRENT_DATE
                WHERE id_alumni = $2
                RETURNING id_tracer
            """, payload.status, payload.id_alumni)

            # 3. Jika status 'Melanjutkan Pendidikan', simpan detail pendidikan
            if payload.status == 'PEND':
                if not payload.detail_pendidikan or not bukti_kuliah:
                    raise HTTPException(
                        status_code=400,
                        detail="Detail pendidikan dan bukti kuliah wajib diisi untuk status 'Melanjutkan Pendidikan'."
                    )

                file_name = f"bukti-kuliah-{payload.id_alumni}.pdf"
                supabase.storage.from_('tracer-study/bukti-kuliah').upload(
                    file=bukti_kuliah.file.read(),
                    path=file_name,
                    file_options={"content-type": "application/pdf"}
                )
                public_bukti_kuliah_url = supabase.storage.from_('tracer-study/bukti-kuliah').get_public_url(file_name)

                await conn.execute("""
                    INSERT INTO detail_pendidikan_tinggi(
                        id_tracer, id_perguruan_tinggi, id_program_studi,
                        tahun_masuk, id_sumber_biaya, bukti_kuliah
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                """, tracer_id, payload.detail_pendidikan.id_perguruan_tinggi,
                     payload.detail_pendidikan.id_program_studi,
                     payload.detail_pendidikan.tahun_masuk,
                     payload.detail_pendidikan.id_sumber_biaya,
                     public_bukti_kuliah_url)
            else:
                public_bukti_kuliah_url = None

            # 4. Insert jawaban kuesioner ke tabel detail_kuesioner
            kuesioner_records = [
                (tracer_id, q_id, a_id)
                for q_id, a_id in payload.kuesioner.items()
            ]

            await conn.copy_records_to_table(
                'detail_kuesioner',
                records=kuesioner_records,
                columns=['id_tracer', 'id_kuesioner', 'id_jawaban']
            )

            return {
                "message": "Data kuesioner berhasil disimpan.",
                "json": payload.dict(),
                "bukti_kuliah": public_bukti_kuliah_url
            }

        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Database transaction failed: {str(e)}"
            )
    await conn.close()


@app.get("/tracer/all", tags=["Tracer"])
async def get_all_alumni_tracer_data():
    """
    Mengambil daftar lengkap semua alumni beserta status tracer, detail pendidikan,
    dan jawaban kuesioner mereka. Sesuai dengan catatan:
    - Alumni yang belum mengisi tracer akan tetap ditampilkan dengan data tracer null/default.
    - Semua pertanyaan kuesioner akan ditampilkan, dengan jawaban `null` jika belum dijawab.
    """
    conn = await get_db()
    try:
        # Query 1: Ambil daftar master semua pertanyaan
        master_questions_query = "SELECT id_kuesioner, pertanyaan FROM kuesioner ORDER BY id_kuesioner;"
        master_questions = await conn.fetch(master_questions_query)

        # Query 2: Ambil data gabungan semua alumni
        alumni_data_query = """
                            SELECT a.id_alumni, \
                                   a.nis, \
                                   a.nisn, \
                                   a.nik, \
                                   a.nama_siswa, \
                                   a.tanggal_lahir, \
                                   a.tahun_lulus, \
                                   a.alamat_email, \
                                   a.no_telepon, \
                                   t.is_filled, \
                                   s.status, \
                                   dpt.tahun_masuk, \
                                   pt.perguruan_tinggi, \
                                   ps.nama_program_studi, \
                                   sb.sumber_biaya, \
                                   dpt.bukti_kuliah, \
                                   (SELECT jsonb_agg(jsonb_build_object('id_kuesioner', k.id_kuesioner, 'jawaban', \
                                                                        j.jawaban))
                                    FROM detail_kuesioner dk
                                             JOIN kuesioner k ON dk.id_kuesioner = k.id_kuesioner
                                             JOIN jawaban j ON dk.id_jawaban = j.id_jawaban
                                    WHERE dk.id_tracer = t.id_tracer) AS answered_questionnaires
                            FROM alumni a
                                     LEFT JOIN tracer t ON a.id_alumni = t.id_alumni
                                     LEFT JOIN status s ON t.kode_status = s.kode_status
                                     LEFT JOIN detail_pendidikan_tinggi dpt ON t.id_tracer = dpt.id_tracer
                                     LEFT JOIN perguruan_tinggi pt ON dpt.id_perguruan_tinggi = pt.id_perguruan_tinggi
                                     LEFT JOIN program_studi ps ON dpt.id_program_studi = ps.id_program_studi
                                     LEFT JOIN sumber_biaya sb ON dpt.id_sumber_biaya = sb.id_sumber_biaya
                            ORDER BY a.tahun_lulus DESC, a.nama_siswa ASC; \
                            """
        alumni_records = await conn.fetch(alumni_data_query)

        # Proses transformasi data di Python
        response_list = []
        for record in alumni_records:
            # Buat lookup map untuk jawaban yang sudah diisi oleh alumni ini
            answered_map = {}
            if record['answered_questionnaires']:
                # `answered_questionnaires` bisa berupa string JSON, perlu di-parse
                answered_list = json.loads(record['answered_questionnaires'])
                answered_map = {item['id_kuesioner']: item['jawaban'] for item in answered_list}

            # Buat daftar kuesioner lengkap untuk alumni ini
            full_questionnaire_list = []
            for question in master_questions:
                full_questionnaire_list.append({
                    "questionnaire": question['pertanyaan'],
                    "answer": answered_map.get(question['id_kuesioner'], None)
                    # Ambil jawaban jika ada, jika tidak -> null
                })

            # Susun objek respons sesuai struktur yang diinginkan
            alumni_detail = {
                "personal_data": {
                    "id_alumni": record['id_alumni'],
                    "nis": record['nis'],
                    "nisn": record['nisn'],
                    "nik": record['nik'],
                    "tanggal_lahir": record['tanggal_lahir'],
                    "nama_siswa": record['nama_siswa'],
                    "tahun_lulus": record['tahun_lulus'],
                    "alamat_email": record['alamat_email'],
                    "no_telepon": record['no_telepon'],
                },
                "tracer_data": {
                    "status": record['status'],
                    "is_filled": record['is_filled'] if record['is_filled'] is not None else False,
                },
                "pendidikan_data": {
                    "perguruan_tinggi": record['perguruan_tinggi'],
                    "program_studi": record['nama_program_studi'],
                    "sumber_biaya": record['sumber_biaya'],
                    "bukti_kuliah": record['bukti_kuliah'] if record['bukti_kuliah'] else None,
                    "tahun_masuk": record['tahun_masuk'],
                } if record['perguruan_tinggi'] else None,
                "questionnaire_data": full_questionnaire_list,
            }
            response_list.append(alumni_detail)

        return response_list

    finally:
        await conn.close()