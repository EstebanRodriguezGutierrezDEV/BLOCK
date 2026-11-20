# Tu block de tareas

Tu espacio para organizarte, con una pantalla de bienvenida y un calendario de tareas por meses y años. **FastAPI** expone una API REST, **Flask** sirve el frontend (HTML, CSS y JavaScript) y **SQLite** almacena los datos de forma persistente.

## Producción en GitHub y Vercel

El proyecto se despliega en Vercel como una aplicación Flask. El archivo `app.py` sirve la web y llama a FastAPI en la misma función. Usa PostgreSQL mediante `DATABASE_URL` para guardar cuentas, sesiones y tareas. El SQLite local se queda en tu equipo.

1. Crea un repositorio vacío en GitHub. Si partes de una carpeta sin historial de Git, ejecuta `git init -b main`, `git add .` y `git commit -m "Preparar Tu block de tareas para Vercel"`. Luego conecta el repositorio y sube la rama `main`. Sustituye el usuario y el nombre del repositorio por los tuyos.

   ```bash
   git remote add origin https://github.com/TU_USUARIO/TU_REPOSITORIO.git
   git push -u origin main
   ```

2. En Vercel, importa el repositorio de GitHub y usa la raíz del proyecto con el preset **Flask**. El runtime toma Python 3.12 de `.python-version` e instala `requirements.txt`. No hace falta configurar un comando de compilación ni iniciar un segundo servidor.
3. Conecta una base PostgreSQL, por ejemplo **Neon** desde el [Marketplace de Vercel](https://vercel.com/docs/marketplace-storage). En **Settings → Environment Variables**, comprueba que **Production** tiene `DATABASE_URL` con la conexión de la base, preferiblemente la URL con pool y TLS de tu proveedor. Si la integración le da otro nombre, crea `DATABASE_URL` con ese mismo valor. Para **Preview**, usa una base o rama separada. Guarda el valor solo en Vercel.
4. Vuelve a desplegar si añadiste la variable después del primer despliegue. Abre la URL de producción, registra una cuenta y prueba guardar una tarea. Al iniciar, la app crea las tablas que faltan. El workflow de GitHub Actions comprueba el registro y las tareas con una base PostgreSQL de prueba.

El primer despliegue usa una base de producción vacía. Las cuentas y tareas de `backend/todos.db` no se importan automáticamente.

Documentación oficial de [Flask en Vercel](https://vercel.com/docs/frameworks/backend/flask) y de [repositorios de GitHub en Vercel](https://vercel.com/docs/git/vercel-for-github).

## Requisitos

- Python 3.10 o superior.
- Dos terminales para ejecutar los servidores en paralelo.

## 1. Instalar dependencias

Abre una terminal en la carpeta del proyecto:

```bash
python -m venv .venv
```

Activa el entorno virtual:

- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`
- **Windows (CMD):** `.venv\Scripts\activate.bat`
- **macOS/Linux:** `source .venv/bin/activate`

A continuación:

```bash
pip install -r requirements.txt
```

## 2. Arrancar el backend (terminal 1)

Desde la raíz del proyecto, con el entorno virtual activado:

```bash
python -m uvicorn backend.main:app --reload --port 8000
```

La API estará disponible en **http://127.0.0.1:8000**, y puedes explorar sus endpoints desde **http://127.0.0.1:8000/docs**.

## 3. Arrancar el frontend (terminal 2)

Abre otra terminal en la raíz del proyecto y activa el mismo entorno virtual. Después:

```bash
python -m flask --app frontend.app run --debug --port 5000
```

Abre **http://127.0.0.1:5000/** en el navegador.

La bienvenida está en `/`, el registro en `/registro` y el inicio de sesión en `/iniciar-sesion`. Crea una cuenta con tu correo y una contraseña de al menos 12 caracteres. Puedes iniciar sesión en cuanto se cree la cuenta.

Ambos servidores deben permanecer encendidos. El navegador se comunica con Flask y Flask envía las peticiones a FastAPI.

## Funciones

- Pantalla de bienvenida «Tu espacio para organizarte».
- Registro con correo y contraseña e inicio y cierre de sesión. Cada cuenta puede acceder a sus propias tareas.
- Crear tareas con título, descripción opcional y fechas obligatorias de inicio y fin.
- Barra izquierda con selector de año y meses desplegables, con el número de tareas de cada mes.
- Calendario mensual: muestra cada tarea en todos los días de su intervalo, incluidas ambas fechas.
- Colores por estado: violeta para las tareas por empezar, azul para las que están en curso, rosa para las vencidas y verde para las completadas.
- Consultar tareas de años anteriores o futuros, también cuando un intervalo atraviese meses o años.
- Seleccionar un día del calendario para ver sus tareas y volver a la vista mensual.
- Marcar tareas como completadas o pendientes.
- Editar títulos, descripciones y fechas. La fecha final debe ser igual o posterior a la inicial.
- Eliminar tareas con un cuadro que muestra cuál vas a eliminar y permite cancelar.
- Buscar por título o descripción dentro del mes seleccionado.
- Filtrar por todas, pendientes o completadas.
- Ver contadores y porcentaje de progreso del mes.
- Guardar las tareas en `backend/todos.db` (se crea automáticamente al iniciar FastAPI).

La actualización conserva las cuentas con contraseña y las tareas asignadas. Si habías empezado a registrarte y aún no tenías contraseña, vuelve a registrarte con el mismo correo. Las tareas de antes de que existieran las cuentas pueden seguir sin propietario. `TODO_LEGACY_OWNER_EMAIL` permite asignarlas al iniciar FastAPI únicamente a una cuenta cuyo correo se verificó en la versión anterior. A las tareas que aún no tenían fechas se les asigna su día de creación como inicio y fin. El color de una tarea se calcula con la fecha local: vence después de su día de fin.

## Endpoints de FastAPI

La API de tareas requiere `Authorization: Bearer <token>`. Para usarla directamente, registra `email` y `password` con `POST /api/auth/register` y obtén un token de sesión con esos campos en `POST /api/auth/login`. Puedes revocarlo con `POST /api/auth/logout`. Desde la web, Flask gestiona la sesión con una cookie HttpOnly.

| Método | Ruta | Función |
|---|---|---|
| `GET` | `/api/tasks` | Obtener la lista |
| `GET` | `/api/tasks/{id}` | Obtener una tarea |
| `POST` | `/api/tasks` | Crear una tarea |
| `PATCH` | `/api/tasks/{id}` | Editar o completar una tarea |
| `DELETE` | `/api/tasks/{id}` | Eliminar una tarea |

Ejemplo para crear una tarea con JSON:

```json
{
  "title": "Aprender FastAPI",
  "description": "Crear una API REST",
  "start_date": "2026-11-20",
  "end_date": "2026-11-25"
}
```

Ejemplo para marcarla como completada con `PATCH /api/tasks/1`:

```json
{ "completed": true }
```

## Estructura

```text
To-Do/
├── app.py                   # Entrada de Vercel
├── vercel.json
├── backend/
│   ├── __init__.py
│   ├── accounts.py           # Registro y sesiones
│   ├── db.py                 # SQLite, PostgreSQL y migraciones
│   └── main.py               # API de tareas
├── frontend/
│   ├── __init__.py
│   ├── app.py                # Servidor Flask
│   ├── embedded_api.py       # FastAPI dentro de la función de Vercel
│   └── templates/
├── public/
│   ├── favicon.ico
│   └── static/              # CSS, JavaScript e iconos
├── tests/
│   ├── test_api.py
│   ├── test_calendar.py
│   ├── test_frontend.py
│   └── test_vercel.py
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

## Pruebas automáticas

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Las pruebas de navegador de `tests/test_calendar.py` son opcionales. Para ejecutarlas:

```bash
pip install playwright
python -m playwright install chromium
python -m pytest tests/test_calendar.py -q
```

También puedes indicar un Chromium ya instalado con la variable `TODO_TEST_BROWSER` (ruta al ejecutable). Las pruebas usan una base de datos temporal y no modifican tus tareas.

Para ejecutar también la prueba del entorno de producción, define `TODO_TEST_POSTGRES_URL` con la conexión directa a una base PostgreSQL de pruebas. El test crea un esquema temporal y lo elimina al terminar.

## Configuración opcional

- `TODO_DB_PATH`: ruta alternativa del archivo SQLite para FastAPI.
- `TODO_API_URL`: URL interna que Flask usa para comunicarse con FastAPI (por defecto `http://127.0.0.1:8000`).
- `TODO_PUBLIC_URL`: origen público del frontend, por ejemplo `https://tareas.example.com`. Configúralo en Flask si la aplicación está detrás de un proxy HTTPS.

Para servir la app fuera de tu equipo, usa HTTPS en `TODO_PUBLIC_URL`, mantén FastAPI accesible solo para Flask y desactiva el modo debug.
