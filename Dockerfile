# Derivex Dashboard
#
# Hem Node (arayuz sunucusu) hem Python (veri koprusu) gerekiyor.
# Node tabanli imaja Python eklemek, tersinden daha kucuk sonuc veriyor.
#
#   docker build -t derivex-dashboard .
#   docker run --rm -p 5173:5173 derivex-dashboard                 # mock veri
#   docker run --rm -p 5173:5173 -e DATA_MODE=1 derivex-dashboard  # canli veri
#
# Canli modda konteynerin disa acik IP'sinin IdealData'da tanimli olmasi gerekir.

FROM node:20-slim

# python3-requests apt'tan kuruluyor: pip, Debian'in yonetilen ortamina
# yazmaya calisirken hata veriyor ve --break-system-packages istemek
# gereksiz bir risk.
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-requests \
    && rm -rf /var/lib/apt/lists/*

# sqlite3 standart kutuphanede gelir ama Debian onu libpython3-stdlib icinde
# tasir; eksik olsaydi kalicilik CALISMA ANINDA sessizce devre disi kalirdi
# (store.py hatayi yutar, akis surer). Derleme aninda patlamasi yegdir.
RUN python3 -c "import sqlite3; print('sqlite3', sqlite3.sqlite_version)"

WORKDIR /app

# Sunucunun kendisi bagimliliksiz calisiyor, ancak Risk sekmesindeki XLSX
# portfoy ice aktarimi node_modules/xlsx dosyasini tarayiciya servis ediyor.
# Kurulmazsa /xlsx.js 404 doner ve ice aktarma sessizce calismaz.
COPY package*.json ./
RUN npm install --omit=dev

COPY . .

# Konteyner disindan erisilebilmesi icin 127.0.0.1 yerine tum arayuzler.
ENV HOST=0.0.0.0 \
    PORT=5173 \
    DATA_MODE=0 \
    PYTHONUNBUFFERED=1

EXPOSE 5173

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||5173)+'/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["python3", "start.py"]
