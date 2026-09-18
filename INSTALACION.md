# Instalar el tablero SPP en otra computadora

Guía para dejar el tablero de **Valor Cuota SPP** funcionando en una
máquina de la red interna, **todo por descarga**: sin USB, sin git y
sin acceso a PyPI.

Al terminar tendrás el tablero abriéndose con doble clic, el libro de
valor cuota completo desde 1993 y la extracción diaria automática a
las 18:00.

Verificado el 2026-09-14 reproduciendo ese entorno completo (Python
3.10.6 portable + los wheels de `pypro_packs`): los tests pasan, la
API levanta, crea el esquema, registra las series y sirve el tablero.

---

## 0. Qué descargar

Cuatro descargas, todas desde el navegador de esa misma máquina:

| Qué | De dónde |
|---|---|
| **El proyecto** | `https://github.com/RicardoSachs/pap/archive/refs/heads/cambios_vc.zip` (2 MB — incluye el tablero ya compilado) |
| **Python 3.10** | `https://github.com/gabdejo/py_versions` → carpeta `3106`. Si esa máquina ya tiene Python 3.10, sáltatelo |
| **Las librerías** | `https://github.com/gabdejo/pypro_packs` — el wheelhouse que ya usas |
| **Lo que falta** | `https://github.com/RicardoSachs/pap/releases/download/deps-py310/dependencias-faltantes-py310.zip` (38 MB) |

Esa última descarga trae cuatro paquetes que **no están** en
`pypro_packs` y el proyecto necesita:

- **`python-multipart`** — sin él la API ni siquiera arranca: FastAPI lo
  exige para recibir archivos, y de eso depende la carga del XLS
  histórico.
- **`playwright`** (con `pyee` y `greenlet`) — la extracción de la SBS.

Descomprime cada zip en una carpeta que puedas ubicar; el instalador
del paso 3 las busca solo si están junto al proyecto o en *Descargas*.

## 1. Programas base

1. **PostgreSQL** — anota la contraseña del usuario `postgres`; deja el
   puerto en 5432.
2. **Google Chrome** — obligatorio: el WAF de la SBS rechaza navegadores
   headless y el Chromium que trae Playwright. Tiene que ser Chrome real.

Python no hace falta instalarlo: el del paso 0 (`py_versions/3106`) es
portable y se usa tal cual.

## 2. Descomprimir el proyecto en su lugar definitivo

Extrae el zip y **renombra la carpeta a `pap`**, en la ruta donde va a
vivir — por ejemplo `C:\Users\<usuario>\Documents\Proyectos\pap`.

Muévelo *ahora*, no después: la tarea programada del paso 7 guarda la
ruta absoluta, y los datos (`data\`) nacen como carpeta hermana del
proyecto.

## 3. Crear el entorno de Python

Doble clic en **`scripts\Instalar entorno.bat`**. Crea el `.venv` con el
Python 3.10 portable e instala todo desde las carpetas del paso 0, sin
tocar internet.

Si no encuentra alguna carpeta te lo dice; en ese caso pásale las rutas:

```
scripts\"Instalar entorno.bat" C:\ruta\pypro_packs C:\ruta\dependencias-faltantes-py310
```

Termina comprobando que todo importa. Las versiones son las de
`requirements-oficina.txt`, alineadas con el wheelhouse (pandas 2.3.3,
FastAPI 0.137.1…).

Las rutas pueden tener espacios (`Program Files`, `Mis Documentos`) y
pueden ser de red (`\\servidor\...`). Las librerías desde la red van
bien: pip las copia dentro del `.venv`. El **Python** conviene tenerlo
en local — el `.venv` recuerda dónde está su intérprete y lo necesita en
cada arranque, también en la tarea programada de las 18:00. Y si la
carpeta venía con un `.venv` de otra máquina, bórralo antes: un entorno
no se puede copiar, sigue apuntando al Python de donde nació.

> `requirements.lock.txt` es **otra cosa**: describe la máquina de
> desarrollo (Python 3.14, pandas 3.x). Esos wheels no existen en
> `pypro_packs` y aquí no hay de dónde bajarlos.

## 4. Configurar la conexión a la base (`.env`)

`.env` no viaja en el zip (contiene credenciales). Créalo copiando la
plantilla:

```
copy .env.example .env
```

y llena al menos:

```
PG_HOST=localhost
PG_PORT=5432
PG_DBNAME=pap
PG_USER=postgres
PG_PASSWORD=<la contraseña de PostgreSQL>
DATA_DIR=C:\Users\<usuario>\Documents\Proyectos\data
MACHINE_ID=LAPTOP-NUEVA
SCRAPER_ENABLED=true
```

Este es **el único archivo que tienes que escribir** en la máquina
nueva. Las dos últimas líneas dicen qué puede hacer esta computadora:
`SCRAPER_ENABLED=true` es lo que habilita la extracción de la SBS, y
`MACHINE_ID` es solo el nombre con el que aparece en los registros.

Si **esa** máquina tiene terminal Bloomberg, agrega también:

```
BLOOMBERG_ENABLED=true
```

y la pestaña *Series Bloomberg* podrá descargar de verdad los
componentes del target y del benchmark. Déjalo fuera en las máquinas sin terminal:
el tablero sigue funcionando igual, solo que esa descarga responde que
no hay terminal aquí.

`DATA_DIR` es opcional pero conviene fijarlo: vacío significa "la
carpeta `data` hermana del proyecto", y si algún día mueves la carpeta
se te quedan atrás el rastro de las extracciones y el perfil de Chrome.

## 5. Crear la base y llenarla

Doble clic en **`scripts\Crear base.bat`**. Lee el `.env`, crea la base
con ese nombre si no existe y le aplica el esquema. Si ya existe no la
toca, y se puede repetir sin miedo.

<details>
<summary>A mano, si prefieres</summary>

Desde **pgAdmin**: en el árbol de la izquierda, clic derecho sobre
*Databases* → *Create* → *Database…*, escribe `pap` en **Database** y
*Save*.

Desde la **consola** (el `bin` de PostgreSQL no suele estar en el PATH,
así que conviene la ruta completa):

```
"C:\Program Files\PostgreSQL\18\bin\createdb.exe" -U postgres pap
```

Te pedirá la contraseña del usuario `postgres`. El nombre tiene que ser
el mismo que pusiste en `PG_DBNAME`. El esquema se crea solo al abrir el
tablero.
</details>

El libro de valor cuota **se reconstruye desde la fuente**, que es
pública:

1. Abre el tablero (paso 6) y ve a **Registro y carga → Valor cuota**.
2. Pulsa *Abrir la página de la SBS* y descarga el Excel
   «Valores cuota desde Agosto 1993».
3. Súbelo en **carga histórica por Excel**. Revisa lo que muestra y
   confirma con *Cargar lo que falta*.

Eso deja el libro completo desde 1993. La extracción diaria se encarga
del resto.

> Lo que no viaja por esta vía es lo que solo existe en la otra
> computadora: las correcciones hechas a mano, el registro de series
> Bloomberg, las series manuales y las composiciones del target y del benchmark. Son
> pocas y se vuelven a declarar desde el tablero en minutos. Si algún
> día quieres moverlas tal cual y tienes cómo pasar un archivo, está
> `scripts\respaldo_spp.py --exportar` / `--importar`.

## 6. Abrir el tablero

Doble clic en **`Tablero SPP.bat`**. Levanta la API, espera a que
responda y abre el navegador en `http://127.0.0.1:8000/spp/`.

Si algo falta, el `.bat` lo dice antes de abrir nada (entorno, `.env`,
dashboard sin compilar). Si la API muere al arrancar, su ventana queda
abierta con el error.

Comprueba: el Panel muestra los KPIs y el gráfico, y **Libro** trae
fechas desde 1993.

## 7. Extracción diaria automática

Doble clic en **`scripts\Programar extraccion SPP.bat`**. Registra la
tarea de Windows *Profuturo - Valor cuota SPP*, todos los días a las
18:00, apuntando a esta copia del proyecto.

Y ahora lo importante, **la primera vez en esta máquina**:

```
scripts\"Programar extraccion SPP.bat" -Probar
```

Quédate mirando. Se abre Chrome; si el WAF de la SBS muestra una
verificación, resuélvela a mano. Esa cookie queda guardada en el perfil
y las corridas siguientes trabajan solas. Mientras no hagas esto, la
tarea de las 18:00 fallará contra el WAF.

Otros comandos del mismo archivo: `-Estado` (qué hay registrado),
`-Hora 19:30` (cambiar la hora), `-Quitar`.

> La SBS exige un Chrome **visible**, así que la sesión de Windows debe
> estar iniciada a esa hora. Con el equipo apagado la corrida se pospone
> y se recupera al volver.

---

## Comprobación final

| Señal | Dónde |
|---|---|
| El libro llega hasta la semana pasada | Pestaña **Libro** |
| Profuturo aparece en color y el resto en grises | Pestaña **Panel** |
| La tarea dice *Ready* y una próxima ejecución | **Registro y carga → Valor cuota**, cuadro "Corrida automática" |
| La extracción escribe rastro | `data\spp\extraccion.log` |

Prueba opcional de que el código está sano en esta máquina (necesita
`pytest`, que no viene en el wheelhouse):

```
.venv\Scripts\python.exe -m pytest tests\prices tests\shared tests\web tests\tradebook -q
```

Deben pasar los 125.

---

## Si algo sale mal

| Síntoma | Causa y arreglo |
|---|---|
| El navegador abre y el tablero está vacío | Falta `.env` o la base no existe. La ventana de la API tiene el error exacto. |
| `MissingSecret: PG_PASSWORD is not set` | El `.env` quedó con la línea en blanco (paso 4). |
| `No se encontro pg_dump` | Agrega `C:\Program Files\PostgreSQL\18\bin` al PATH. |
| `El scraper no esta habilitado en esta maquina` | Falta `SCRAPER_ENABLED=true` en el `.env` (paso 4). Reinicia el tablero después: la configuración se lee al arrancar. |
| La extracción falla con "El WAF de la SBS bloqueo la peticion" | Haz el paso 7 con el operador delante. |
| "Google Chrome no esta instalado" | Instala Chrome real (paso 1.2). |
| `ModuleNotFoundError: multipart` o la API no arranca | Falta la descarga de `dependencias-faltantes-py310` (paso 0). Vuelve a correr el instalador con las dos carpetas. |
| `Could not find a version that satisfies...` al instalar | La carpeta de `pypro_packs` está incompleta, o el Python no es 3.10 de 64 bits. |
| `there is no unique or exclusion constraint matching the ON CONFLICT specification` | A la base le faltan restricciones que el código necesita, porque la creó una versión anterior. Doble clic en **`scripts\Verificar esquema.bat`**: compara tu base con el esquema del proyecto, dice exactamente qué falta y se ofrece a agregarlo. |
| El tablero dice que la tarea apunta a otra copia del proyecto | Quedaron dos carpetas del zip. Vuelve a correr `scripts\Programar extraccion SPP.bat` desde la definitiva. |
| La página se ve vieja tras actualizar el proyecto | Ctrl+F5 una vez. (La API ya pide revalidar el HTML; solo pasa si el navegador guardó algo de antes de esta versión.) |

## Actualizar el proyecto más adelante

La dinámica es de dos pasos, sin git en esta máquina:

1. En la computadora donde se trabaja: subir los cambios a GitHub.
2. Aquí: bajar otra vez el mismo zip

   ```
   https://github.com/RicardoSachs/pap/archive/refs/heads/cambios_vc.zip
   ```

   y descomprimirlo **encima** de la carpeta `pap`, aceptando reemplazar.

`.env`, `.venv\` y la carpeta `data\` sobreviven: los dos primeros
porque el zip no los trae, la tercera porque vive fuera del proyecto.
Las librerías tampoco hay que reinstalarlas, salvo que el proyecto
estrene alguna dependencia nueva.
Al volver a abrir `Tablero SPP.bat`, la API aplica sola los cambios de
esquema y registra lo que falte — no hay paso de migración que se pueda
olvidar, y la base y su contenido no se tocan.

Un detalle: descomprimir encima **no borra** los archivos que
desaparezcan del proyecto. En la práctica solo significa que se van
acumulando piezas viejas del tablero compilado (llevan un código en el
nombre, así que no estorban). Si alguna vez quieres dejarlo limpio,
borra la carpeta `pap` entera salvo `.env` y `.venv`, y descomprime.
