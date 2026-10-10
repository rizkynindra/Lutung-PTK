# LUTUNG PTK

Portal untuk menemukan, mencoba, dan mengintegrasikan teknologi PTK.

## Menjalankan secara lokal

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
$env:LUTUNG_DEMO_MODE = "true"
uvicorn app.main:app --reload
```

Buka `http://127.0.0.1:8000`.

## Pengujian

```powershell
ruff check .
pytest
```

## Docker pada VM internal

```powershell
Copy-Item .env.example .env
docker compose up --build -d
docker compose ps
```

Port hanya diikat ke loopback VM. Letakkan reverse proxy organisasi di depan aplikasi untuk TLS,
hostname resmi, batas ukuran request, dan kebijakan akses jaringan.

## Mengaktifkan layanan PTK

1. Minta pemilik layanan meninjau `contracts/ptk-technologies.openapi.yaml`.
2. Konfirmasi URL, autentikasi, timeout, format respons, error, health check, dan retensi.
3. Atur URL serta token di `.env`.
4. Set `LUTUNG_DEMO_MODE=false`.
5. Jalankan pengujian kontrak terhadap lingkungan nonproduksi.

Jangan menandai teknologi sebagai `available` sebelum kontrak dan kebijakan retensinya disetujui.
Konfigurasi katalog berada di `app/config/technologies.yaml`.

## Batas data

LUTUNG tidak menyimpan unggahan ke penyimpanan persisten. Parser multipart dapat memakai ruang
sementara selama permintaan; container menempatkan `/tmp` pada `tmpfs`. Logging hanya mencatat
metode, path, status, durasi, dan request ID. Retensi pada layanan tujuan tetap harus dinyatakan
oleh pemilik layanan.


## DEPLOY TO OPENSHIFT

docker build --platform linux/amd64 -t lutung-ptk:v1.0.2 .

oc login

docker login -u pti-dev -p $(oc whoami -t) default-route-openshift-image-registry.apps.ocp-drc.bpjsketenagakerjaan.go.id

docker tag lutung-ptk:v1.0.2 default-route-openshift-image-registry.apps.ocp-drc.bpjsketenagakerjaan.go.id/ptk1/lutung-ptk:v1.0.2

docker push default-route-openshift-image-registry.apps.ocp-drc.bpjsketenagakerjaan.go.id/ptk1/lutung-ptk:v1.0.2