#!/bin/bash
# Instalación del grabador xlive en un Ubuntu Server 24.04 limpio.
#
# Uso (como root en la máquina nueva):
#   apt-get update && apt-get install -y git
#   git clone <url-del-repo> /opt/xlive
#   bash /opt/xlive/deploy/setup.sh
#
# Vuelve a ser seguro ejecutarlo: no sobrescribe config.json ni .env.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

echo "== 1/6 Dependencias del sistema =="
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip ffmpeg

echo "== 2/6 Entorno virtual =="
if [ ! -x myenv/bin/python3 ]; then
    python3 -m venv myenv
fi
myenv/bin/pip install --quiet --upgrade pip
myenv/bin/pip install --quiet -r requirements.txt

echo "== 3/6 Configuración =="
if [ ! -f config.json ]; then
    echo '[]' > config.json
    echo "   config.json creado vacío: las rooms se agregan desde la web."
else
    echo "   config.json ya existe, se respeta ($(grep -c '"username"' config.json || true) rooms)."
fi

if [ ! -f .env ]; then
    cp deploy/env.example .env
    echo "   .env creado desde la plantilla."
fi

# Genera la clave de la web si todavía está el marcador.
if grep -q '^STRMNTR_PASSWORD=GENERAR-EN-INSTALACION' .env; then
    NEWPASS="$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 16)"
    sed -i "s|^STRMNTR_PASSWORD=.*|STRMNTR_PASSWORD=${NEWPASS}|" .env
    GENERATED="$NEWPASS"
else
    GENERATED=""
fi

echo "== 4/6 Carpeta de grabaciones =="
RECDIR="$(grep -E '^STRMNTR_DOWNLOAD_DIR=' .env | cut -d= -f2-)"
RECDIR="${RECDIR:-downloads}"
mkdir -p "$RECDIR"
echo "   grabaciones en: $(cd "$RECDIR" && pwd)"
df -h "$RECDIR" | tail -1 | awk '{print "   disco: "$2" total, "$4" libres ("$5" usado)"}'

echo "== 5/6 Servicio systemd =="
sed "s|__DIR__|$DIR|g" deploy/streamonitor.service > /etc/systemd/system/xlive.service
systemctl daemon-reload
systemctl enable xlive.service >/dev/null
echo "   servicio xlive.service instalado."

echo "== 6/6 Arranque =="
systemctl restart xlive.service
sleep 6
systemctl --no-pager --lines=0 status xlive.service || true

IP="$(hostname -I | awk '{print $1}')"
PORT="$(grep -E '^STRMNTR_PORT=' .env | cut -d= -f2- || true)"
PORT="${PORT:-5000}"
echo
echo "================================================================"
echo " Listo. Abrid en el navegador de cualquier equipo de la red:"
echo "     http://${IP}:${PORT}/"
echo " Usuario: admin"
if [ -n "$GENERATED" ]; then
    echo " Clave:   $GENERATED     <-- apuntadla, está en $DIR/.env"
else
    echo " Clave:   la que ya tengáis en $DIR/.env"
fi
echo
echo " Control por consola:"
echo "     cd $DIR && myenv/bin/python3 Controller.py status"
echo " Registro en vivo:"
echo "     journalctl -u xlive -f"
echo "================================================================"
