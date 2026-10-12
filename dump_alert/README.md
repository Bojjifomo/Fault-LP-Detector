# Cek risiko LP sebelum tidur — Robinhood Chain

Tempel contract address → koinnya **dibandingkan dengan kondisi CRADLE, PULP & JOLLY tepat
sebelum crash**, dicek price action dan aksi whale-nya, lalu dapat vonis
**LAYAK / HATI-HATI / JANGAN** untuk membuka posisi LP yang akan ditinggal tidur.

## 1. Website (Streamlit)

### Jalan di komputer sendiri

```bash
pip install -r dump_alert/requirements.txt
export ALCHEMY_API_KEYS=key1,key2     # satu atau beberapa key; atau isi di sidebar website
streamlit run dump_alert/streamlit_app.py
```

Buka http://localhost:8501, tempel contract address, klik **Cek sekarang**.

Tampilannya bergaya Hyperliquid (gelap, aksen teal). Tema dipasang lewat CSS di dalam
`streamlit_app.py`, bukan `.streamlit/config.toml`, supaya app Streamlit lain di repo ini tidak ikut berubah
dan tampilan tetap gelap walau browser memakai mode terang.

### Fokus pool USDG

Analisa memakai **pool token/USDG** (tempat LP dibuka), bukan pool ETH:
- Dari semua pool USDG koin itu, dipakai yang likuiditasnya terdalam. Harga, market cap, saran range, dan
  simulasi nilai LP dalam USD/USDG. Daftar semua pool USDG (fee, porsi swap, likuiditas) ada di bagian
  **Semua pool USDG**.
- Riwayat sebelum pool USDG ada diambil dari pool utama (biasanya pool launch vs ETH) dan dikonversi ke
  USDG per sampel lewat pool ETH/USDG. Dengan begitu ATH dan pola lama tetap terhitung. Pool USDG baru
  dipakai 1 jam sesudah dibuat. Di HYDX, harga pool USDG dan pool utama selisihnya ±3%.
- Sampel dari pool yang sedang kosong (likuiditas 0, harganya bisa sampai 1e50) dibuang dan diganti
  pool utama.
- Koin tanpa pool USDG aktif dapat peringatan. Analisanya tetap jalan memakai pool utama, nilai USD-nya
  pakai harga ETH hari ini. Pool USDG dicari ulang tiap 6 jam, karena sering baru dibuat belakangan.
- Data lama yang harganya masih dalam ETH otomatis disampel ulang sekali.

Yang ditampilkan:
1. **Vonis** dan sarannya.
2. **Dibanding CRADLE, PULP & JOLLY sebelum crash**: tabel tanda-tanda yang terlihat di puncak
   tepat sebelum crash, dan berapa yang juga ada di koin ini sekarang.
3. **Kenapa vonisnya begitu**, dalam kalimat biasa. Kalau harga **tembus ATH di chart 4 jam** (di atas
   close candle 4 jam tertinggi sebelumnya), muncul label 📈 dan vonisnya minimal HATI-HATI.
4. **Saran range LP**: batas bawah (min price) yang tidak tembus di 80% / 90% kasus serupa selama
   jam tidur, plus peluang range pilihanmu tembus. Juga zona support yang ada di atas batas bawah, dan
   alternatif batas bawah teknikal tepat di bawah zona support terdekat.
5. **Support & likuiditas** (lihat bagian di bawah).
6. Grafik 72 jam: market cap, aksi whale, dan vonis tiap 5 menit.
7. **Kalau ditinggal tidur**: peluang turun, dan berapa nilai LP-mu turun kalau harga jatuh.

**Mundur ke waktu** (opsional): isi mis. `2026-10-10 15:35` untuk melihat vonis seolah kamu
mengecek pada jam itu. Contoh: PULP di jam itu dapat "Mirip sekali dengan CRADLE sebelum crash −86%"
dan vonis JANGAN.

Lama pengecekan: koin yang pertama kali dicek ±1–3 menit (transfer ditarik paralel, harga lama
disampel lebih jarang). Cek berikutnya untuk koin yang sama cuma beberapa detik. Kalau terputus
(rate limit, kuota habis), data yang sudah ditarik tetap tersimpan: klik **Cek sekarang** lagi untuk
melanjutkan dari titik terakhir.

### Online (Streamlit Community Cloud, gratis)

1. https://share.streamlit.io → **Create app** → repo `Bojjifomo/Fault-LP-Detector`, branch `main`,
   *Main file path* `dump_alert/streamlit_app.py`.
2. **Advanced settings → Secrets**:
   ```toml
   ALCHEMY_API_KEYS = ["key-1", "key-2", "key-3"]   # atau satu saja: ALCHEMY_API_KEY = "key-1"
   ```
3. **Deploy**. Dependensi diambil dari `dump_alert/requirements.txt`.
4. Set app ke **private** (Settings → Sharing). Kalau publik, semua pengunjung memakai kuota Alchemy-mu.

Sidebar menampilkan status tiap key (aktif / kuota bulanan habis / ditolak) dan CU yang sudah dipakai.
Key tambahan juga bisa diisi di sidebar (pisahkan dengan koma). Key itu hanya berlaku untuk sesi browser
tersebut.

### Banyak key & kuota

- Tiap request memakai key yang paling longgar. Key yang kena 429 (batas CU/detik) dipelankan otomatis.
  Key yang kuota bulanannya habis, salah, atau belum mengaktifkan jaringan Robinhood Chain dilewati
  (dicoba lagi sejam kemudian).
- Batas CU/detik berlaku **per app**, kuota bulanan (free tier 30 juta CU) berlaku **per akun**. Beberapa
  app di satu akun hanya menambah kecepatan, bukan kuota.
- Memakai banyak akun gratis untuk menggabung kuota bisa melanggar ketentuan Alchemy dan membuat akun
  diblokir. Cek Terms of Service-nya dulu. Alternatif resminya: paket berbayar (pay-as-you-go).

Perkiraan pemakaian (diukur di HYDX: 135 ribu transfer, umur 17 hari):

| | CU |
|---|---|
| Koin baru, pertama kali dicek | ±60–70 ribu (transfer ±20 rb, klasifikasi alamat ±14 rb, cari pool USDG + harga ±32 rb, peta likuiditas ±2 rb) |
| Cek ulang koin yang sama (data masih ada) | ±0,3–3 ribu |
| `watch` tiap 5 menit, 1 koin | ±2 juta per bulan (`--interval 900` → ±0,6 juta) |

Jadi 30 juta CU cukup untuk ±450 koin baru per bulan. Di Streamlit Cloud, data hilang saat app tidur
atau reboot, jadi koin yang dicek lagi sesudahnya dihitung sebagai koin baru. Penghematan yang sudah
dipasang:
- progres disimpan per tahap dan per potongan harga, jadi gagal di tengah tidak mengulang dari nol;
- sampel harga yang sejak sampel sebelumnya tidak ada swap tidak dibaca (harga pool tidak berubah);
- hasil cek kontrak vs wallet disimpan bersama untuk semua koin (`data/_addresses.json`);
- yang diklasifikasi hanya alamat yang pernah ≥0,3% supply (batas whale) atau sangat aktif;
- puncak lama yang sudah dirapatkan tidak dibaca ulang;
- dua pengecekan koin yang sama tidak menarik data dua kali; harga ETH di-cache 10 menit.

### Support & likuiditas

Dua sumber yang digabung jadi zona support (±4%) di bawah harga sekarang:

- **Likuiditas LP sungguhan.** Peta tick Uniswap v4 dibaca langsung dari chain untuk sampai 8 pool
  USDG & ETH token itu (likuiditas aktif + liquidityNet tiap tick, dari harga sekarang sampai −95%).
  Dari situ dihitung berapa USD yang harus dijual untuk menurunkan harga ke tiap level. Pool-pool ini
  saling terhubung lewat arbitrase, jadi kedalamannya dijumlah.
  - **Tembok likuiditas** = pita yang ≥2x lebih tebal dari pita di sekitarnya.
  - **Lubang likuiditas** = rentang ≥10% yang hampir kosong.
  - **Wallet terbesar jual semua** = penurunan harga kalau seluruh token wallet terbesar (dan top-10)
    dilempar sekaligus.
  - Biaya ±1–5 ribu CU, dibaca ulang paling sering tiap 15 menit.
- **Price action.** Swing low 7 hari terakhir (terendah dalam ±2 / ±8 jam) yang memantul ≥10%,
  plus harga dengan volume transaksi tinggi.

Skor kekuatan: pantulan terakhir <24 jam = 2 poin (1–3 hari = 1), pantulan ≥40% = 1, likuiditas zona
≥1,5x / ≥2,5x pita sekitarnya = 1 / 2. Kuat = skor ≥3, sedang = 2, lemah ≤1.
Kekuatan zona dikalibrasi di riwayat CRADLE, PULP & JOLLY (`python -m dump_alert.lp_check --support-test`).
Yang paling menentukan adalah seberapa baru zona terakhir memantul dan besar pantulannya. Jumlah
pantulan dan volume hampir tidak berpengaruh. Likuiditas LP tebal ikut menambah (berdasarkan mekanika
pool; likuiditas historis tidak tersimpan jadi belum bisa diuji).

| Zona (saat disentuh dalam 24 jam) | Bertahan* — CRADLE/PULP/JOLLY | HYDX (di luar kalibrasi) |
|---|---|---|
| kuat (skor ≥3) | 38% | 75% |
| sedang | 28% | 41% |
| lemah | 0% | 39% |
| level biasa (bukan zona) | 14% | 27% |

\* Bertahan = 6 jam sesudah disentuh harga tidak tembus >5% di bawah zona dan sempat memantul ≥5%.
Di koin yang rawan dump, support lebih sering jebol daripada bertahan. Pakai untuk memilih batas bawah
range, bukan sebagai jaminan.

## 2. Tanda sebelum crash (dasar perbandingan)

Dilihat di puncak harga, tepat sebelum crash terdalam tiap token pembanding (crash saat launch tidak
dihitung, crash tanpa minimal 3 tanda tidak dipakai):

| Tanda | Artinya |
|---|---|
| Naik tajam | harga naik ≥20% dalam 4 jam |
| Sudah berlipat | harga ≥1,8x dari titik terendah 24 jam |
| Di pucuk | ≤10% di bawah puncak 24 jam |
| Pola retest ATH | balik ke dekat ATH setelah sempat turun ≥25% |
| Whale memborong | whale beli bersih ≥0,9% supply dalam 6 jam (harga dipompa) |
| Whale menjual | whale jual bersih ≥1,5% supply dalam 6 jam |
| Dompet besar baru | dompet besar baru masuk ≥1,5% supply dalam 2 jam |
| Tembus ATH 4 jam | harga di atas close candle 4 jam tertinggi sebelumnya (4 dari 6 crash pembanding terjadi tepat setelah ini) |

Kemiripan = berapa tanda crash pembanding yang juga ada di koin sekarang. Kemiripan ≥80% menaikkan
vonis LAYAK jadi HATI-HATI.

Uji silang (tiap token hanya dicocokkan ke crash token **lain**; peluang turun ≥50% dalam 8 jam):

| Token | kemiripan <50% | kemiripan ≥80% | |
|---|---|---|---|
| JOLLY | 3% | **34%** | terbukti (JOLLY tidak dipakai saat menyusun aturan) |
| CRADLE | 12% | **25%** | terbukti |
| PULP | 14% | 1% | belum terbukti (baru 1 crash besar) |

### Menambah token pembanding

```bash
python -m dump_alert.fetch --full --token 0xCA     # resolusi harga penuh, sekali saja (±5–10 menit)
# tambahkan 0xCA ke dump_alert/reference_tokens.txt
python -m dump_alert.lp_check --build-reference    # perbarui reference.json (ikut di-commit)
```

## 3. Command line & alarm

```bash
python -m dump_alert.lp_check 0xCA [--range 20] [--sleep 8] [--at "2026-10-10 15:35"]
python -m dump_alert.lp_check --backtest          # uji vonis di token pembanding
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python -m dump_alert.watch --token 0xCA [--interval 900]
```

Semua perintah membaca `ALCHEMY_API_KEYS` (beberapa key, dipisah koma) atau `ALCHEMY_API_KEY`.

`watch` mengirim pesan saat vonis memburuk (mis. jadi JANGAN), sinyal untuk bangun dan tarik LP.
Dari posisi yang dibuka saat LAYAK lalu tetap kena drop ≥30%, alert JANGAN muncul sebelum harga
tembus −30% di 76% kasus CRADLE dan 79% kasus PULP.

## Filter di balik vonis

| | Filter |
|---|---|
| **K1** ❌ | whale jual bersih ≥1,5% supply dalam 6 jam |
| **K2** ❌ | harga anjlok ≥20% dalam 4 jam |
| **K3** ❌ | ≤10% di bawah puncak 24 jam setelah naik ≥2x |
| **K4** ❌ | < 2 jam sejak launch |
| **K5** ❌ | zona retest ATH: balik ke dekat puncak sebelumnya (tembus ≤5%) setelah turun ≥25% |
| **K6** ❌ | retest ATH gagal / tembus support |
| L1–L6 ⚠️ | whale jual 1 jam, harga liar, pump ≥50%, dompet besar baru, riwayat sering dump, risiko launch |
| **L7** ⚠️ | tembus ATH chart 4 jam: K5/K6 tidak memicu JANGAN, tapi vonis minimal HATI-HATI |

Satu ❌ → **JANGAN**; dua atau lebih ⚠️ (atau L7) → **HATI-HATI**; selain itu **LAYAK**.

Catatan tembus ATH 4 jam: di data pembanding, sesudah tembus ATH 4 jam harga turun ≥30% dalam 8 jam
di 56% kasus CRADLE dan 84% JOLLY (PULP 10%). Karena itu tembus ATH tidak dianggap aman.

### Saran range

Batas bawah = penurunan terdalam selama jam tidur yang tidak terlampaui di 80% (dan 90%) kasus,
dihitung dari riwayat koin itu sendiri dan dari token pembanding dengan vonis yang sama; diambil yang
lebih dalam, dibulatkan ke 5%. Ini peluang, bukan jaminan: di puncak PULP 10/10, saran −50% (90%)
tetap tertembus karena PULP turun −77% dalam 8 jam.

Backtest (turun dalam 8 jam sesudah titik masuk):

| Vonis | CRADLE ≥30% | PULP ≥30% | JOLLY ≥30% | gabungan ≥30% / ≥50% |
|---|---|---|---|---|
| LAYAK | 26% | 16% | 0% | 13% / 3% |
| HATI-HATI | 40% | 9% | 22% | 24% / 5% |
| JANGAN | 52% | 42% | 28% | 39% / 14% |

## Batasan

- Baru 3 token pembanding. Tambah sampel crash lain supaya perbandingannya makin bisa dipercaya.
- **LAYAK bukan jaminan**: koin bisa crash tanpa tanda. Tetap pasang alarm.
- Harga disampel tiap 5 menit (3 hari terakhir), lebih jarang untuk riwayat lama (3 puncak
  tertinggi disampel ulang rapat supaya ATH tidak terlewat); wick sangat singkat bisa tidak tertangkap.
  Sampel tanpa transfer token ke/dari pool memakai harga sebelumnya; swap multi-hop yang tidak
  memindahkan token ini (jarang) bisa terlewat.
- Pool utama token daos.world/Pons sering ber-fee LP 0 (fee diambil hook); program memberi tanda ini.
- Pool USDG dikenali dari sampel ±165 transaksi (banyak dari yang terbaru). Pool USDG yang sangat jarang
  dipakai swap bisa tidak terdeteksi.
- Likuiditas pool USDG = likuiditas aktif di harga sekarang, dihitung setara full range (perkiraan kedalaman).
- Data pembanding (CRADLE, PULP, JOLLY) masih dari pool lamanya. Tanda dan persentase turunnya tidak
  bergantung satuan, jadi perbandingannya tetap berlaku.
- Koin yang masih di bonding curve (belum punya pool Uniswap) belum bisa dinilai.

Cek logika tanpa jaringan: `python -m dump_alert.selftest`.
