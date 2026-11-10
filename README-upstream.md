# StreaMonitor
A Python3 application for monitoring and saving (mostly adult) live streams from various websites.

Inspired by [Recordurbate](https://github.com/oliverjrose99/Recordurbate)

## Supported sites
| Site name     | Abbreviation | Aliases                     | Quirks                 | Selectable resolution |
|---------------|--------------|-----------------------------|------------------------|-----------------------|
| Bongacams     | `BC`         |                             |                        | Yes                   |
| Cam4          | `C4`         |                             |                        | Yes                   |
| Cams.com      | `CC`         |                             |                        | Currently only 360p   |
| CamSoda       | `CS`         |                             |                        | Yes                   |
| Chaturbate    | `CB`         |                             |                        | Yes                   |
| DreamCam      | `DC`         |                             |                        | No                    |
| DreamCam VR   | `DCVR`       |                             | for VR videos          | No                    |
| FanslyLive    | `FL`         |                             |                        | Yes                   |
| Flirt4Free    | `F4F`        |                             |                        | Yes                   |
| MyFreeCams    | `MFC`        |                             |                        | Yes                   |
| SexChat.hu    | `SCHU`       |                             | use the id as username | No                    |
| StreaMate     | `SM`         | PornHubLive, PepperCams,... |                        | Yes                   |
| StripChat     | `SC`         | XHamsterLive,...            | must add crypto keys   | Yes                   |
| StripChat VR  | `SCVR`       |                             | for VR videos          | No                    |
| XLoveCam      | `XLC`        |                             |                        | No                    |

Currently not supported:
* Amateur.TV (They use Widevine now)
* Cherry.tv (They switched to Agora)
* ImLive (Too strict captcha protection for scraping)
* LiveJasmin (No nudity in free streams)
* ManyVids Live (They switched to Agora)

There are hundreds of clones of the sites above, you can read about them on [this site](https://adultwebcam.site/clone-sites-by-platform/).

## Requirements
* Python 3
  * Install packages listed in requirements.txt with pip.
* FFmpeg

## Usage

The application has the following interfaces:
* Console
* External console via ZeroMQ (sort of working)
* Web interface

#### Starting and console
Start the downloader (it does not fork yet)\
Automatically imports all streamers from the config file.
```
python3 Downloader.py
```

On the console you can use the following commands:
```
add <username> <site> - Add streamer to the list (also starts monitoring)
remove <username> [<site>] - Remove streamer from the list
start <username> [<site>] - Start monitoring streamer
start * - Start all
stop <username> [<site>] - Stop monitoring
stop * - stop all
status - Status display 
status2 - A slightly more readable status table
quit - Clean exit (Pressing CTRL-C also behaves like this)
```
For the `username` input, you usually have to enter the username as represented in the original URL of the room. 
Some sites are case-sensitive.

For the `site` input, you can use either the full or the short format of the site name. (And it is case-insensitive)

#### "Remote" controller
Add or remove a streamer to record (Also saves config file)
```
python3 Controller.py add <username> <website>
python3 Controller.py remove <username>
```

Start/stop recording streamers
```
python3 Controller.py <start|stop> <username>
```

List the streamers in the config
```
python3 Controller.py status
```

#### Web interface

You can access the web interface on port 5000. 
If set password in parameters.py username is admin, password admin, empty password is also allowed.
When you set the WEBSERVER_HOST it is also accesible to from other computers in the network

## Docker support

You can run this application in docker. I prefer docker-compose so I included an example docker-compose.yml file that you can use.
Simply start it in the folder with `docker-compose up`.

## Configuration

You can set some parameters in the [parameters.py](parameters.py).

You also have to add decryption keys yourself for StripChat in the `stripchat_mouflon_keys.json` file.

## Disclaimer

This program is only a proof of concept and education project, I don't encourage anybody to use it. \
Most (if not every) streamers disallow recording their shows. Please respect their wish. \
If you don't, and you record them despite this request, please don't ever publish or share any recordings. \
If you either record or share the recorded shows, you might be legally punished. \
Also, please don't use this tool for monetization in any way.

## Watchdog de salas (Telegram)

Vigila el estado de cada sala de `config.json` y avisa por Telegram cada vez que
una se conecta o se desconecta, llevando la cuenta de las horas de transmisión.

No consulta los sitios por su cuenta: lee el estado que los bots ya mantienen
(`bot.sc`), así que no añade carga ni puede contradecir a lo que está grabando.

### Puesta en marcha

1. Crea un bot con [@BotFather](https://t.me/BotFather) y copia el token.
2. Escríbele al bot (o añádelo al grupo) y abre
   `https://api.telegram.org/bot<TOKEN>/getUpdates` para ver el `chat.id`.
   Los grupos tienen id negativo, p. ej. `-1001234567890`.
3. Ponlos en `.env`:

   ```
   STRMNTR_TG_TOKEN=123456789:AAF...
   STRMNTR_TG_CHAT_ID=-1001234567890
   ```

4. Reinicia el servicio: `sudo systemctl restart streamonitor`
5. Comprueba el envío: `python3 Controller.py wdtest`

### Avisos

```
🟢 CONECTADA · Savage_Room_X [SC]
🕒 09:14:03 · offline previo 11h 20m

🔴 DESCONECTADA · Savage_Room_X [SC]
🕒 17:02:41 · transmitió 7h 48m
```

`PUBLIC` y `PRIVATE` cuentan como transmisión, así que pasar a show privado no
genera aviso ni corta el conteo. Un cambio debe mantenerse
`STRMNTR_WD_DEBOUNCE_OFF` segundos (90 por defecto) antes de anunciarse, para
que un corte de red de un minuto no llene el chat; el evento se guarda con la
hora real del cambio, no con la de la confirmación. Los estados `ERROR` y
`RATELIMIT` significan "no se sabe", no "desconectada", y no generan aviso.

### Resumen diario

A las **15:00** se envía el resumen de la ventana **06:00 del día anterior →
06:00 de hoy**, ordenado por horas. Solo se listan las salas que transmitieron;
las demás únicamente cuentan para el total:

```
📊 Resumen de transmisión
🗓 lun 17 ago 06:00 → mar 18 ago 06:00

1. Savage_Room_X [SC]
    ⏱ 7h 48m · 2 ses · 09:14→17:02 · 32%
...
🟢 Con transmisión: 31 de 44 salas
Σ Total: 214h 05m
x̄ Promedio (activas): 6h 54m
```

Si el watchdog estuvo caído parte de la ventana, ese tiempo se descuenta y se
avisa al pie: un apagón no se factura como horas al aire. Si el proceso estaba
parado a las 15:00, el resumen se envía en cuanto arranca, una sola vez por día.

### Comandos

```
python3 Controller.py wdstatus              # quién está en línea y desde cuándo
python3 Controller.py wdreport              # reenviar el resumen de hoy
python3 Controller.py wdreport 2026-08-15   # el resumen de un día concreto
python3 Controller.py wdtest                # probar la conexión con Telegram
```

### Ajustes (`.env`)

| Variable | Def. | Qué hace |
|---|---|---|
| `STRMNTR_WATCHDOG` | `true` | Activa el watchdog |
| `STRMNTR_TG_TOKEN` / `STRMNTR_TG_CHAT_ID` | — | Credenciales de Telegram |
| `STRMNTR_WD_TZ` | `America/Bogota` | Zona horaria de avisos y resumen |
| `STRMNTR_WD_REPORT_HOUR` | `15` | Hora de envío del resumen |
| `STRMNTR_WD_DAY_START_HOUR` | `6` | Hora de corte de la ventana |
| `STRMNTR_WD_ONLINE` | `PUBLIC,PRIVATE` | Estados que cuentan como transmisión |
| `STRMNTR_WD_DEBOUNCE_OFF` | `90` | Segundos antes de dar por desconectada |
| `STRMNTR_WD_DEBOUNCE_ON` | `10` | Segundos antes de dar por conectada |
| `STRMNTR_WD_UNKNOWN_TIMEOUT` | `900` | Segundos de error antes de asumir offline |
| `STRMNTR_WD_ALERTS` | `true` | `false` deja sólo el resumen diario |
| `STRMNTR_WD_GRACE` | `90` | Espera al arrancar antes de fijar el estado inicial |
| `STRMNTR_WD_DB` | `watchdog.db` | Fichero del historial (SQLite) |
| `STRMNTR_WD_RETENTION` | `120` | Días de historial que se conservan |
