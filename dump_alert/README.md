# Fault LP Detector — cek risiko LP sebelum tidur (Robinhood Chain)

Tempel contract address → koinnya dinilai **OK / CAUTION / AVOID** untuk membuka posisi LP yang akan
ditinggal tidur, lengkap dengan peluang dump, likuiditas & support, rekomendasi range, situasi mirip di
24 koin sampel, dan technical view. Tampilan website berbahasa Inggris; dokumentasi ini berbahasa Indonesia.

## 1. Website (Streamlit)

### Jalan di komputer sendiri

```bash
pip install -r dump_alert/requirements.txt
export ALCHEMY_API_KEYS=key1,key2     # satu atau beberapa key; atau isi di sidebar website
streamlit run dump_alert/streamlit_app.py
```

Buka http://localhost:8501, tempel contract address, klik **Check now**.

Tampilannya bergaya Hyperliquid (gelap, aksen teal). Tema dipasang lewat CSS di dalam
`streamlit_app.py`, supaya tetap gelap walau browser memakai mode terang.

### Online (Streamlit Community Cloud, gratis)

1. https://share.streamlit.io → **Create app** → repo `Bojjifomo/Fault-LP-Detector`, branch `main`,
   *Main file path* `dump_alert/streamlit_app.py`.
2. **Advanced settings → Secrets**:
   ```toml
   ALCHEMY_API_KEYS = ["key-1", "key-2", "key-3"]   # atau satu saja: ALCHEMY_API_KEY = "key-1"
   ```
3. **Deploy**. Dependensi diambil dari `dump_alert/requirements.txt`.
4. Set app ke **private** (Settings → Sharing). Kalau publik, semua pengunjung memakai kuota Alchemy-mu.
   Repo ini public: jangan pernah menaruh key di file, hanya di Secrets.

### Yang ditampilkan (urut dari atas)

1. **Kotak keputusan** paling atas: satu jawaban jelas (✅ OK to open / ✅ risiko crash rendah tapi fee terlalu
   kecil / ⚠️ Risky / 🚫 Don't open), 4 poin alasan dalam bahasa sederhana (risiko crash, range terbaik dalam
   dolar, holder besar, arus uang 4 jam), dan **What to do**. Kalau model bilang risiko rendah tapi situasi mirip
   banyak yang crash, ada peringatan untuk memakai jumlah lebih kecil.
2. Market cap, jarak dari ATH, 4 jam terakhir, ukuran pool USDG.
3. **How likely is a crash while you sleep?**: peluang dari model, hasil 30 situasi paling mirip, dan seberapa
   sering crash terjadi setelah vonis yang sama saat diuji.
4. **What pushes the risk up or down?**: tanda bahaya, faktor yang menaikkan risiko, dan yang menurunkannya.
5. **How far can the price move while you sleep?** (baru): turun & naik terjauh selama jam tidur untuk separuh /
   8 dari 10 / 9 dari 10 malam, dari riwayat koin itu dan koin sampel dengan vonis sama.
6. **Who holds the coin?** (baru): 10 wallet terbesar (bukan pool/kontrak): porsi supply, market cap saat mereka
   beli (harga beli rata-rata), untung berapa kali, gerakan terakhir, perubahan 24 jam.
7. **Is money coming in or going out?** (baru): beli, jual, bersih, jumlah pembeli/penjual, transaksi terbesar,
   dan beli/jual bersih wallet besar untuk 1 / 4 / 24 jam terakhir.
8. **Liquidity and price floors**: profil likuiditas vertikal ala VPVR, zona support, dampak jual wallet terbesar.
9. **Which LP range should you open?**: **range aman** — range tersempit yang tidak keluar range (OOR) sepanjang
   jam tidur di ≥90% malam serupa (bisa 80/90/95%), ditambah **jarak aman 5–10%** di kedua sisi. Ditampilkan
   seberapa sering tetap di dalam range, seberapa sering menyentuh batas bawah, hasil dalam dolar, dan
   perbandingan dengan batas bawah yang biasa kamu pakai (−60/−65/−70%), range rencanamu, range dengan rata-rata
   tertinggi (sering OOR), dan full range.
10. **What happened after similar moments?**, **Does it look like a coin right before a crash?** (sekarang
    menyebut tanda yang masih kurang), **Chart and on-chain indicators**.
11. Grafik 72 jam, **If you sleep** (nilai posisimu dalam dolar kalau harga turun 30% / 50%), alarm, rincian filter.

Bahasa tampilan dibuat sederhana: kalimat pendek tanpa istilah teknis, angka hasil dalam dolar, dan penjelasan
metode dipindah ke bagian lipat **How is this calculated?**.

Tiap analisa ditutup dengan **Conclusion** (kesimpulan satu-dua kalimat dari datanya, warnanya mengikuti
tingkat risikonya).

**Position size (USD)** (di form): dipakai untuk menghitung porsi fee-mu; posisi besar di pool tipis
mendapat fee lebih kecil per dolar karena likuiditasmu sendiri ikut membagi fee.
**Extra careful mode** (toggle di form, dulu *Conservative mode*): vonis = yang paling berat dari aturan & model.
**Rewind to a past time** (opsional): nilai koin seolah kamu mengecek pada jam itu.

Lama pengecekan: koin yang pertama kali dicek ±1–5 menit; cek berikutnya untuk koin yang sama cuma
beberapa detik. Kalau terputus (rate limit, kuota habis), data yang sudah ditarik tetap tersimpan: klik
**Check now** lagi untuk melanjutkan.

## 2. Vonis: model + pengaman

Vonis dasar = peluang turun ≥30% dalam jam tidur dari model logistik yang dilatih di 24 koin sampel:
**< 15% → OK, 15–35% → CAUTION, ≥ 35% → AVOID**. Pengaman: kalau filter keras aturan menyala (whale jual
bersih ≥1,5% supply/6 jam, harga anjlok ≥20%/4 jam, di pucuk setelah 2x, < 2 jam sejak launch, zona
retest ATH / retest gagal), vonis minimal CAUTION. Mode konservatif: vonis = yang paling berat dari
aturan & model.

Isi model (21 fitur): jarak dari ATH, kelipatan dari low 24 jam, naik/turun 4 jam, volatilitas, dompet
besar baru, whale bersih 6 jam, umur koin, jumlah pembeli & penjual unik 2 jam, supply yang dipegang
holder untung ≥3x, sisa supply wallet jam pertama, lonjakan volume, cadangan pool / supply, tembus ATH
4 jam, pola retest ATH, jam dalam sehari, dominasi pembeli terbesar, lebar Bollinger, RSI 1 jam.

### Hasil uji (leave-one-token-out: tiap koin dinilai oleh model yang dilatih tanpa koin itu)

AUC = seberapa baik momen yang diikuti dump ≥30% dalam 8 jam diurutkan di atas momen yang tidak
(0,5 = tebak koin, 1 = sempurna). Median per koin:

| | semua titik (24 koin) | 19 koin baru* | saat koin ramai (≥30 pembeli/2 jam) |
|---|---|---|---|
| Aturan lama saja | 0,70 | 0,72 | 0,53 |
| **Model** | **0,86** (lebih baik di 22/24 koin) | **0,91** (19/19) | **0,77** (21/23) |

\* 19 koin dari user yang sama sekali tidak dipakai saat memilih fitur model — uji paling bersih.

Kalibrasi (prediksi model vs dump yang benar-benar terjadi, semua koin, leave-one-token-out):

| Model bilang | 0–10% | 10–20% | 20–35% | 35–50% | 50–70% | 70–100% |
|---|---|---|---|---|---|---|
| Dump nyata | 3% | 13% | 32% | 43% | 58% | 70% |

Saat koin ramai (saat kamu biasa LP), dibanding aturan lama:

| | waktu OK | dump saat OK | dump saat AVOID | dump yang lolos di OK |
|---|---|---|---|---|
| Aturan lama | 23% | 10,6% | 35,9% | 8,8% |
| **Model + pengaman (default)** | **30%** | **7,8%** | **55,3%** | **8,4%** |
| Mode konservatif | 18% | 5,4% | 40,7% | 3,5% |

Jumlah koin sampel vs akurasi (saat koin ramai): 3 koin 0,71 → 6 koin 0,74 → 12 koin 0,76 → 18 koin 0,77
→ 23 koin 0,77. Akurasi sudah mendatar; sampel tambahan paling berguna kalau jenisnya berbeda (koin yang
selamat, atau koin yang pernah kamu LP-kan beserta hasilnya). Kontribusi tiap koin sendiri-sendiri kecil
(±0,002 AUC): model tidak bergantung pada satu koin.

### Data apa yang membantu, apa yang tidak (24 koin, AUC median per koin & arah sama di berapa koin)

| Membantu | AUC | | Tidak membantu | AUC |
|---|---|---|---|---|
| dekat ATH | 0,85 (24/24) | | EMA gap / tren EMA | ±0,49 |
| pembeli & penjual unik 2 jam | 0,85 (24/24) | | VWAP | 0,50 |
| umur koin (makin muda makin rawan) | 0,84 (23/24) | | divergence RSI | 0,50 |
| cadangan pool kecil dibanding supply | 0,84 (22/24) | | pola retest ATH sendirian | 0,50 |
| volatilitas 6 jam | 0,80 (23/24) | | perubahan likuiditas 6 jam | 0,50 |
| wallet jam pertama masih pegang | 0,79 (19/24) | | | |
| lebar Bollinger | 0,78 (22/24) | | | |
| dominasi pembeli terbesar rendah | 0,78 (23/24) | | | |
| sudah berlipat dari low 24 jam | 0,74 (23/24) | | | |
| holder untung ≥3x | 0,71 (20/24) | | | |
| dompet besar baru | 0,71 (24/24) | | | |

Indikator teknikal klasik (RSI, EMA, VWAP, struktur swing, divergence) hampir tidak memprediksi dump ≥30%
dalam 8 jam di koin-koin ini; di website ditampilkan sebagai konteks.

### Transaksi LP tidak dihitung sebagai whale jual/beli

Di Uniswap v4, tambah likuiditas terlihat seperti jual dan tarik likuiditas seperti beli di transfer token
(di HYDX 22% transaksi besar ternyata transaksi LP). Receipt transaksi besar (≥0,1% supply) dalam 24 jam
terakhir dicek sekali; yang berisi ModifyLiquidity tanpa Swap tidak dihitung sebagai whale jual/beli.

## 3. Situasi mirip

Setiap jam di riwayat 24 koin sampel (15.551 momen, `dump_alert/analogs.json.gz`) dibandingkan dengan
kondisi sekarang lewat lima sisi: **pattern** (tanda sebelum crash, retest ATH, tembus ATH 4 jam),
**price action** (bentuk grafik 24 jam), **whales** (jual/beli bersih whale 6/12/18/24 jam, dompet besar
baru, holder untung besar, wallet jam pertama), **technicals** (RSI, EMA, VWAP, Bollinger, struktur,
volume, porsi beli) dan **coin profile** (umur, keramaian, cadangan pool, volatilitas). Diambil 30 momen
terdekat (maksimal satu per koin per 12 jam) dan dilihat apa yang terjadi 8 jam sesudahnya.

Uji leave-one-token-out (median AUC): semua sisi 0,832; tanpa whale 0,843; tanpa technicals 0,834; tanpa
pattern 0,831; tanpa price action 0,823; tanpa profil 0,828. Versi yang dipakai (tanpa whale, sisi yang datanya
kosong dihitung tidak mirip): 0,842. Hasil situasi mirip dihitung dari pattern,
price action, technicals & profil; kemiripan whale tetap ditampilkan sebagai konteks.

## 4. Likuiditas, support & range

### Pool USDG

Analisa memakai **pool token/USDG** terdalam (tempat LP dibuka). Riwayat sebelum pool USDG ada diambil
dari pool utama (biasanya pool launch vs ETH) dan dikonversi ke USDG per sampel. Sampel dari pool yang
sedang kosong (likuiditas 0, harganya bisa sampai 1e50) dibuang. Koin tanpa pool USDG aktif dapat
peringatan; pool USDG dicari ulang tiap 6 jam.

### Support & likuiditas

- **Likuiditas LP sungguhan**: peta tick Uniswap v4 sampai 8 pool USDG & ETH (dari +200% sampai −95% harga
  sekarang) → berapa USD yang harus dijual untuk menurunkan harga ke tiap level, dan dibeli untuk
  menaikkannya. Ditampilkan sebagai profil vertikal per pita 2%, di samping volume 7 hari per harga. Tembok likuiditas = pita
  ≥2x lebih tebal dari sekitarnya; lubang likuiditas = rentang ≥10% yang hampir kosong.
- **Price action**: swing low 7 hari yang memantul ≥10%, plus harga dengan volume tinggi.
- Skor kekuatan (dikalibrasi di 24 koin): jumlah pantulan 7 hari 3+ = strong, 1–2 = medium, tanpa
  pantulan (hanya zona volume/likuiditas) = weak; likuiditas zona ≥2,5x pita sekitarnya menaikkan satu
  tingkat. Seberapa baru & besar pantulan ternyata tidak konsisten (temuan lama dari 3 koin tidak terbukti).

| Zona (saat disentuh dalam 24 jam) | Bertahan* (24 koin) |
|---|---|
| strong (3+ pantulan) | 42% |
| medium (1–2 pantulan) | 37% |
| weak (tanpa pantulan) | 19% |
| level biasa (bukan zona) | 32% |

\* Bertahan = 6 jam sesudah disentuh harga tidak tembus >5% di bawah zona dan sempat memantul ≥5%.
Di koin yang rawan dump, support lebih sering jebol daripada bertahan. Pakai untuk memilih batas bawah
range, bukan sebagai jaminan.

### Range aman (jarang keluar range) + jarak aman 5–10%

Ini yang dipakai sebagai saran utama di website:

1. Untuk tiap batas bawah −1..−95%, dicari batas atas terkecil supaya harga **tidak pernah keluar range
   sepanjang jam tidur** di ≥90% malam (pilihan 80/90/95% di form). Dihitung dari dua sumber sekaligus:
   situasi serupa di koin sampel (vonis × volatilitas yang sama, jalur harga per 15 menit) dan riwayat koin itu
   sendiri (per 5 menit). Yang dipakai adalah yang lebih ketat.
2. Dari semua range yang lolos, dipilih yang **paling sempit** (likuiditas paling terkonsentrasi = fee terbesar
   selama harga di dalam range).
3. Ditambah **jarak aman**: 5% di kedua sisi saat risiko rendah, 10% saat vonis CAUTION/AVOID atau
   volatilitas tinggi. Varian 5% dan 10% dua-duanya ditampilkan.
4. Batas bawah −60/−65/−70% (kebiasaan user) dan range rencanamu ditampilkan sebagai pembanding.

Uji leave-one-coin-out (range dipilih hanya dari koin lain, dicek di koin X, jendela 8 jam, target 90%):

| Range | Tetap di dalam range sepanjang malam | Menyentuh batas bawah |
|---|---|---|
| Range tersempit (tanpa jarak) | 89,4% | 4,6% |
| + jarak 5% | 92,5% | 2,6% |
| + jarak 10% | 94,2% | 1,5% |
| batas bawah −60% (batas atas sama) | 94,0% | 1,1% |
| batas bawah −65% | 94,4% | 0,7% |
| batas bawah −70% | 94,7% | 0,4% |

Target 90% benar-benar menghasilkan ±90% di koin yang tidak dilihat. Untuk jendela 8 jam, batas bawah
−60..−70% jarang tersentuh; range aman biasanya lebih sempit (fee lebih besar) dengan sedikit lebih sering
menyentuh batas bawah. Tabel di website menunjukkan keduanya dalam dolar supaya bisa dipilih.

### Range dengan rata-rata tertinggi (riset sebelumnya)

Pertanyaannya: dari harga sekarang, range LP mana yang hasilnya paling baik kalau ditinggal tidur?
Dijawab dengan simulasi di riwayat 24 koin sampel (`dump_alert/ranges.py`):

- Tiap jam di riwayat koin sampel (14.554 momen; koin yang volumenya < 0,2% market cap per hari tidak
  dipakai), dibuka posisi LP dengan **133 range**: batas bawah −5..−90% × batas atas +2..+200%, plus full
  range, lalu ditinggal 2–12 jam (sesuai input).
- **Nilai posisi** di akhir dihitung dengan rumus Uniswap v3/v4 (hanya tergantung harga akhir).
- **Fee** hanya saat harga di dalam range, sebanding dengan volume nyata per 15 menit sesudahnya
  (dibanding rata-rata 24 jam sebelumnya, dibatasi 20x), dikali konsentrasi likuiditas range itu, dikali
  **tingkat fee pool hari ini** (fee pool × volume 24 jam ÷ likuiditas aktif semua pool, diukur live), dan
  dikurangi efek posisimu sendiri yang ikut membagi fee (input Position size).
- Range terbaik = yang paling menumbuhkan modal kalau diulang tiap malam di situasi serupa (rata-rata
  log(1 + hasil), tiap koin sama berat), dikelompokkan per vonis × volatilitas 6 jam. Kriteria ini
  menghukum kerugian besar lebih berat daripada rata-rata biasa. Kalau range terbaik pun negatif, saran
  yang muncul adalah **tidak membuka LP**.

Uji leave-one-coin-out (range untuk koin X dipilih hanya dari 23 koin lain), 8 jam. Pertumbuhan per malam / peluang rugi >20%:

| Tingkat fee (full range) · posisi vs pool | Range optimal (+ skip) | Full range | −40% / +50% | Optimal > full range |
|---|---|---|---|---|
| 1%/hari · 0,5% | **+0,57%** / 0,6% | −0,88% / 3,8% | −3,69% / 5,6% | 19 dari 24 koin |
| 1%/hari · 2% | **−0,03%** / 0,1% | −0,88% / 3,8% | −3,76% / 5,6% | 18 dari 24 |
| 2%/hari · 0,5% | **+2,02%** / 0,8% | −0,63% / 3,7% | −2,64% / 4,9% | 20 dari 24 |
| 2%/hari · 2% | **+0,52%** / 0,6% | −0,64% / 3,7% | −2,77% / 5,0% | 18 dari 24 |
| 4%/hari · 0,5% | **+5,85%** / 2,9% | −0,13% / 3,6% | −0,60% / 4,3% | 21 dari 24 |
| 4%/hari · 2% | **+3,24%** / 1,3% | −0,14% / 3,6% | −0,86% / 4,4% | 22 dari 24 |
| 4%/hari · 10% | **+0,73%** / 0,4% | −0,21% / 3,6% | −1,86% / 4,7% | 18 dari 24 |

Temuan utama:

- **Range simetris yang lebar-sedang (±20%, −40/+50) rugi rata-rata** di koin sampel kecuali fee-nya
  sangat tinggi: LP itu "short volatility". Kalau harga jatuh menembus range, kamu memegang koin penuh;
  kalau harga pump, untungnya terpotong.
- **Range yang sebagian besar di bawah harga menang** (batas atas +2..+5%): posisinya kebanyakan USDG,
  mendapat fee sambil membeli koin pelan-pelan saat turun. Ini mencerminkan koin sampel yang rata-rata
  turun sesudah momen ramai; kalau kamu yakin akan pump, baris "best two-sided range" menyimpan lebih
  banyak upside.
- Range sempit (−5..−25%) hanya terbayar saat tingkat fee tinggi (≥4%/hari), vonis OK, dan volatilitas
  rendah.
- Di fee ≤1%/hari, hampir tidak ada range yang untung, jadi saran yang paling sering muncul adalah skip.
  Contoh PRIORS (fee 1%, ±0,5%/hari): skip.

## 5. Command line, alarm & koin sampel
## 5. Command line, alarm & koin sampel

```bash
python -m dump_alert.lp_check 0xCA [--range 20] [--sleep 8] [--at "2026-10-10 15:35"]
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python -m dump_alert.watch --token 0xCA [--interval 900] [--conservative]
python -m dump_alert.selftest                      # cek logika tanpa jaringan
```

Semua perintah membaca `ALCHEMY_API_KEYS` (beberapa key, dipisah koma) atau `ALCHEMY_API_KEY`. `watch`
mengirim pesan saat vonis memburuk (sinyal untuk bangun dan tarik LP), memakai vonis yang sama dengan
website.

Koin sampel ada di `dump_alert/reference_tokens.txt` (dipilih user). Menambah koin:

```bash
python -m dump_alert.fetch --full --token 0xCA          # resolusi harga penuh, sekali saja
# tambahkan 0xCA ke dump_alert/reference_tokens.txt, lalu:
python -m dump_alert.lp_check --build-reference          # reference.json: tanda crash, model, uji (±20 menit)
python -m dump_alert.analogs --build                     # library situasi mirip (±15 menit)
python -m dump_alert.ranges --build --eval               # riset range optimal: ranges.npz + uji (±15 menit)
python -m dump_alert.study dump_alert/data/0x...json ... [--final] [--value all]   # uji fitur & kontribusi koin
```

## 6. Banyak key & kuota Alchemy

- Tiap request memakai key yang paling longgar; key yang kena 429 dipelankan otomatis, key yang kuota
  bulanannya habis / salah dilewati (dicoba lagi sejam kemudian). Sidebar menampilkan status tiap key.
- Batas CU/detik berlaku per app, kuota bulanan (free tier 30 juta CU) per akun. Memakai banyak akun
  gratis untuk menggabung kuota bisa melanggar ketentuan Alchemy.
- Perkiraan: koin baru ±60–150 ribu CU (tergantung panjang riwayat), cek ulang koin yang sama ±0,3–3 ribu,
  `watch` tiap 5 menit ±2 juta per bulan per koin (`--interval 900` → ±0,6 juta). Menarik 24 koin sampel
  dengan resolusi penuh memakai ±3 juta CU.

## Batasan

- **OK bukan jaminan**: koin bisa crash tanpa tanda. Tetap pasang alarm.
- Model dilatih di 24 koin dari ekosistem yang sama dan periode yang berdekatan; kondisi pasar yang sangat
  berbeda bisa menurunkan akurasinya.
- Harga disampel tiap 5 menit; wick sangat singkat bisa tidak tertangkap. Sampel tanpa transfer token
  ke/dari pool memakai harga sebelumnya.
- Peta likuiditas, support likuiditas, dan dampak jual whale hanya tersedia untuk kondisi sekarang (mode
  mundur waktu memakai likuiditas hari ini).
- Pool USDG dikenali dari sampel ±165 transaksi; pool yang sangat jarang dipakai swap bisa tidak terdeteksi.
- Koin yang masih di bonding curve (belum punya pool Uniswap) belum bisa dinilai.
- Range optimal: tingkat fee pool diukur hari ini dan dianggap tetap selama jam tidur; volume dianggap
  terbagi ke pool sebanding likuiditasnya; perubahan likuiditas LP lain selama posisi terbuka tidak
  dihitung; biaya gas dan slippage saat membuka/menutup tidak dihitung. Hasilnya mencerminkan perilaku
  24 koin sampel yang sebagian besar turun seiring waktu.
