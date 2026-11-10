# REcorder xlive

Graba automáticamente las rooms que elijas y deja los vídeos listos para
editar en el disco de esta máquina. Desde el navegador puedes agregar rooms,
ver lo grabado, **cortar** por minutos y **unir** vídeos.



---

## Puesta en marcha (una sola vez)

En un Ubuntu Server 24.04 limpio, como root:

```bash
apt-get update && apt-get install -y git
git clone git@github.com:pyunior/xlive-recorder.git /opt/xlive
bash /opt/xlive/deploy/setup.sh
```

El instalador deja el servicio arrancado y en el último paso imprime la
**dirección y la clave** para entrar copiala.

### Antes de nada: el disco

Las grabaciones ocupan **unos 2,7 GB por hora y por room transmitiendo**. Con
5 rooms grabando 8 horas al día son unos **107 GB al día, 3,2 TB al mes**.

Por eso, antes de arrancar conviene apuntar las grabaciones al disco grande.
En `/opt/xlive/.env`:

```
STRMNTR_DOWNLOAD_DIR=/datos/grabaciones
```

Y reiniciar: `systemctl restart xlive`

**Nada se borra nunca de forma automática.** Cuando el disco baje del 5 %
libre, el grabador se detiene y lo avisa en el registro, pero no toca ningún
fichero. Hacer sitio es decisión propia

---

## Uso diario

Abrir en el navegador la dirección que dio el instalador
(`http://<ip-de-la-maquina>:5000/`). Usuario `admin` y la clave generada.

### Agregar una room

En la pantalla principal, el formulario de arriba: nombre de la room y sitio.
Queda vigilada y se graba sola cada vez que se conecte, sin que nadie tenga
que estar pendiente.

### Cortar un vídeo

Entrar en una room, marcar con la casilla los vídeos que quiera, poner los
minutos en **«Cortar cada \_\_ min»** y pulsad **Cortar**.

Aparecen ficheros nuevos llamados `...-parte000.mp4`, `...-parte001.mp4`, etc.
**El original no se toca.**

El corte **no recodifica**: un vídeo de 10 GB se parte en segundos y no se
pierde nada de calidad. A cambio, los trozos no salen exactos al minuto: el
corte cae en el fotograma clave más cercano, con unos segundos de margen.

> Las grabaciones ya nacen troceadas en bloques de 20 minutos, así que lo
> normal es que no necesites cortar nada. Para cambiar ese tamaño, en `.env`:
> `STRMNTR_SEGMENT_TIME=1200` (en segundos; 1200 = 20 min).

### Unir vídeos

Marcad dos o más y pulsar **Unir**. Se unen en orden cronológico y el
resultado se llama `<primero>-unido.mp4`.

**Puede que  diga que no se pueden unir.** No es un fallo: ocurre cuando los
vídeos no tienen el mismo formato, normalmente porque la modelo **giró la
cámara** y una parte está en vertical (1080x1440) y otra en horizontal
(1920x1080). Unirlos obligaría a recodificar, que es lento y pierde calidad.
El aviso os dice qué formatos hay y cuántos ficheros de cada uno, para que
unáis por separado los de cada grupo.

### Trabajos

Debajo de la barra se ve la cola: *en cola*, *procesando*, *listo* o *error*.
Se procesan de uno en uno a propósito, para que cortar o unir no robe disco a
las grabaciones en curso. Si un trabajo no cabe en el disco, se rechaza con un
aviso **antes** de empezar — nunca borra nada para hacer sitio.

---

## Si algo va mal

```bash
systemctl status xlive          # ¿está corriendo?
journalctl -u xlive -f          # registro en vivo
systemctl restart xlive         # reiniciar
cd /opt/xlive && myenv/bin/python3 Controller.py status   # estado de las rooms
```

**Apagar siempre con `systemctl stop xlive`.** El apagado limpio termina de
convertir las grabaciones en curso a `.mp4`, y puede tardar unos minutos si hay
varias grabando. Si se corta la luz o se mata el proceso a lo bruto, quedan
ficheros `.tmp.ts` a medias; son recuperables, avisad y se recuperan.

### La conexión a internet

Cada room transmitiendo consume **6 Mbps de bajada sostenidos**. Cinco rooms a
la vez son 30 Mbps todo el rato. Si la línea no da para las rooms que tengs,
las grabaciones saldrán con cortes y no hay ajuste que lo arregle: hay que
reducir rooms o ampliar la línea.

---

## Ajustes (`/opt/xlive/.env`)

| Variable | Def. | Qué hace |
|---|---|---|
| `STRMNTR_DOWNLOAD_DIR` | `downloads` | Dónde se guardan las grabaciones |
| `STRMNTR_SEGMENT_TIME` | `1200` | Tamaño del bloque al grabar, en segundos |
| `STRMNTR_MIN_FREE_SPACE` | `5.0` | % libre al que se detiene (no borra) |
| `STRMNTR_HOST` | `0.0.0.0` | `0.0.0.0` la deja visible en la red local |
| `STRMNTR_PORT` | `5000` | Puerto de la web |
| `STRMNTR_PASSWORD` | — | Clave de la web (usuario `admin`) |
| `STRMNTR_RESOLUTION` | `1080` | Calidad que se intenta grabar |

Tras cualquier cambio: `systemctl restart xlive`

