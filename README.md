# SiJuri — Sistema de Seguimiento de Expedientes

Backend + PWA móvil para seguimiento en vivo de expedientes judiciales desde **sijuri.irsacorp.com.ar**.

## Arquitectura

```
┌──────────────────┐    scrape     ┌──────────────────┐    REST API    ┌──────────────────┐
│  sijuri.irsacorp  │ ◄─────────── │  Backend Flask   │ ─────────────► │  PWA (tu celu)   │
│  .com.ar          │  cada 15 min │  + Scraper BS4   │   JSON limpio  │  iOS / Android   │
└──────────────────┘               └──────────────────┘                └──────────────────┘
                                          │
                                    cache en disco
                                   (data/cases.json)
```

## Inicio Rápido

### 1. Configurar credenciales

```bash
cd sijuri-backend
cp .env.example .env
nano .env   # ← completá SIJURI_USER y SIJURI_PASS
```

### 2. Instalar y ejecutar

```bash
pip install -r requirements.txt
python app.py
```

El servidor arranca en **http://localhost:3000** sirviendo la PWA + API.

### 3. Abrir la PWA

- Abrí **http://localhost:3000** desde tu celular (misma red WiFi)
- O desde el navegador de la PC
- En iOS: Safari → Compartir → **"Agregar a pantalla de inicio"**
- En la app, andá a **⚙️ Config** y poné la URL del servidor

### Con Docker

```bash
docker build -t sijuri .
docker run -d -p 3000:3000 --env-file .env --name sijuri sijuri
```

## API Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/cases` | Listar expedientes con filtros |
| `GET` | `/api/cases/:id` | Detalle con movimientos |
| `GET` | `/api/stats` | Estadísticas por estado/estudio |
| `POST` | `/api/refresh` | Forzar re-scrape inmediato |
| `GET` | `/api/status` | Estado del scraper |
| `GET` | `/health` | Health check |

### Filtros disponibles en `/api/cases`

```
GET /api/cases?status=activo&q=IRSA&estudio=Capdevila&limit=20&offset=0
```

## Estructura del Proyecto

```
sijuri-backend/
├── app.py              ← Servidor Flask + API REST
├── scraper.py          ← Scraper adaptado a sijuri.irsacorp.com.ar
├── static/
│   ├── index.html      ← PWA completa (instalar en iOS)
│   └── api-client.js   ← Módulo JS de conexión a la API
├── data/               ← Cache en disco (auto-generado)
├── requirements.txt
├── Dockerfile
├── .env.example
└── README.md
```

## Personalización del Scraper

Si algo no funciona al primer intento, el scraper tiene estrategias de
detección automática pero puede necesitar ajustes menores.

### Login

El scraper auto-detecta:
- La URL del login (prueba /login, /, /auth/login)
- Los nombres de campos (busca type="text" y type="password")
- Tokens CSRF (busca inputs hidden)

Si falla: abrí DevTools en la página de login, inspeccioná el `<form>` y
ajustá los campos en `scraper.py` → método `login()`.

### Tabla de expedientes

El scraper maneja dos modos:
1. **DataTables AJAX** → detecta la URL ajax en los scripts y pide datos JSON
2. **HTML directo** → parsea la tabla `.projects-table` del DOM

Columnas esperadas: Estudio Jurídico | Actor | Demandado | Objeto | Tipo | Acciones

### Detalle del expediente

Extrae de la página de detalle:
- Estado (badge "ESTADO DEL EXPEDIENTE")
- Fecha última acción (badge "FECHA DE ULTIMA ACCION JUDICIAL")  
- Número de expediente
- Etapa del Proceso
- Probabilidad de Éxito
- Última Acción realizada
- Plan de Acción
- Historial de movimientos (si hay tabla o timeline)

## Deploy en Producción

### Opción 1: VPS (recomendado)

```bash
# En tu VPS (DigitalOcean, Linode, etc.)
git clone <tu-repo>
cd sijuri-backend
cp .env.example .env && nano .env
docker build -t sijuri .
docker run -d -p 3000:3000 --env-file .env --restart always --name sijuri sijuri
```

### Opción 2: Railway / Render

1. Subí el código a GitHub
2. Conectá el repo en Railway.app o Render.com
3. Configurá las variables de entorno (SIJURI_USER, SIJURI_PASS, etc.)
4. Deploy automático

### Opción 3: Fly.io

```bash
fly launch
fly secrets set SIJURI_USER=xxx SIJURI_PASS=xxx
fly deploy
```

## Troubleshooting

| Problema | Solución |
|----------|----------|
| Login falla | Verificar credenciales en `.env`. Probar login manual en el navegador |
| No encuentra tabla | Ejecutar con `DEBUG_MODE=true` y revisar logs |
| Pocos expedientes | DataTables puede paginar server-side. El scraper lo maneja automáticamente |
| Timeout | Aumentar `timeout` en `SijuriScraper.__init__()` (default: 30s) |
| CORS error en PWA | El backend ya incluye headers CORS. Verificar que la URL sea correcta |
