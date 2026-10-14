"""
Panduan membaca halaman hasil (tab "How to read the results" di website), dalam bahasa Inggris dan Indonesia.
Urutan bagian mengikuti urutan halaman hasil; judul bagian sama persis dengan judul di halaman (bahasa Inggris)
supaya mudah dicocokkan. Teks boleh berisi <b> (HTML), tanpa data dinamis.
"""

GUIDE = {
    "en": {
        "lang": "English",
        "intro": "Read the page from the top. The first box gives the answer; everything below explains why. "
                 "You don't need to read every section every time.",
        "quick_title": "Quick start (30 seconds)",
        "quick": [
            "Look at the <b>colored box at the top</b> and its title.",
            "Read <b>What to do</b> at the bottom of that box. It already combines everything else on the page.",
            "If you open: use the <b>safe range</b> it shows, put in an amount you can afford to lose, and turn on the "
            "<b>alarm</b>.",
            "Plan your exit: single-sided USDG exits by itself when the price comes back to today's price. Two-sided: the "
            "<b>Take profit</b> line shows the resistance to sell at (set your upper bound just under it).",
            "Already have a position open? Type the market cap when you opened it in <b>Already open?</b> (with your lower "
            "bound in the slider): you get one answer (hold / take profit / close) and a simple price ladder.",
        ],
        "pos_title": "Your position type — and what the upper bound is",
        "pos": [
            "<b>Upper bound</b> = the top of your range (the highest price where your liquidity works). <b>Lower bound</b> = "
            "the bottom.",
            "<b>Single-sided USDG</b> (the default): you add only USDG. The range runs from <b>today's price (0%) down to your "
            "lower bound</b>, e.g. 0% to -65%. So the upper bound is simply 0% (today's price) and you only choose the lower "
            "bound.",
            "What happens then: if the price falls into your range, your USDG buys the coin bit by bit and earns fees. Below "
            "the lower bound you hold only coin and stop earning. If the price goes <b>up</b>, nothing happens: you stay 100% "
            "USDG (no loss, no fees).",
            "The exit is automatic: if the price dips and comes back to today's price, the LP sells the coin back into USDG by "
            "itself.",
            "<b>Two-sided</b>: you add USDG and the coin, and the range goes below and above today's price (e.g. -65% / +65%). "
            "Then the upper bound is where your coin gets sold, which is why the take-profit section suggests an upper bound "
            "just under a resistance.",
            "Choose the type with <b>Position type</b> above the form on the Check a coin tab.",
        ],
        "boxes_title": "The 4 possible answers",
        "boxes": [
            ("good", "✅ OK to open an LP", "Low chance of a crash and the fees pay for the risk. Open the safe range."),
            ("info", "✅ Low crash risk — but fees are too low", "Not dangerous, but not worth it now. Better wait."),
            ("warn", "⚠️ Risky — only small", "Medium crash chance or a danger sign. Wait, or go very small."),
            ("bad", "🚫 Don't open an LP now", "High chance (35%+) of a 30%+ drop while you sleep."),
        ],
        "sections_title": "Section by section (same order as the page)",
        "what": "What it shows", "how": "How to read it", "good": "Good sign", "bad": "Warning sign",
        "sections": [
            ("The answer box (top)",
             "One answer for right now: open, wait, be careful or don't open — with 4 short reasons.",
             ["The color and title are the answer (see the 4 answers above).",
              "<b>Crash risk</b>: our model's chance that the price falls 30% or more while you sleep.",
              "<b>Safe range</b>: the range to use, how often it stayed in range (single-sided: how often the price never "
              "fell to the bottom), and the result in dollars for your position size.",
              "<b>Exit</b> (single-sided) or <b>Take profit</b> (two-sided): how the position closes.",
              "<b>Big holders</b> and <b>Money flow</b>: who could sell and where money is going.",
              "<b>What to do</b>: the action. Read this first."],
             ["Green box with \"Open the safe range …\"."],
             ["Red box, or the line \"similar past moments crashed more often than the model says\" (then use a smaller "
              "amount)."]),
            ("Your position: when to exit",
             "Only when you fill in <b>Already open?</b>: one answer for your open position, then a price ladder.",
             ["<b>The colored box</b> is the answer: <b>Hold — it exits by itself at …</b> (the top of your range, where "
              "your LP is back to 100% USDG), <b>Take profit by hand at …</b> (a strong resistance sits below the top of your "
              "range), <b>Nothing to do</b> (the price is above your range: you are 100% USDG) or <b>Close it now</b> "
              "(verdict AVOID).",
              "<b>The ladder</b> reads like a price chart, top to bottom: ✅ top of your range, 🎯 take-profit level, 📍 now, "
              "⚠️ close level, 🔻 bottom of your range. Each row: the market cap, what happens there, what your LP is worth "
              "there, and how often the price got there in a night like this.",
              "<b>Close if it falls below this</b>: a strong floor that the price reached on 1 in 4 nights or fewer — a normal "
              "dip does not reach it, so if the price breaks it, something is wrong.",
              "Dollar amounts leave out the fees your LP already earned (Uniswap shows them).",
              "<b>Details: every level</b> (click to open) lists all resistances too."],
             ["\"Hold — it exits by itself\" with a high \"happens on\", and the close level far away."],
             ["\"Close it now\", or the price already near the close level."]),
            ("Coin tiles",
             "Four numbers about the coin right now.",
             ["<b>Market cap</b>: price × all coins.",
              "<b>From all-time high</b>: -2% means right at the top (riskier); -60% means far below the top.",
              "<b>Last 4 hours</b>: a big + means it just pumped.",
              "<b>USDG pool size</b>: money in the pool you LP in. Bigger = harder to push the price around."],
             ["Pool size in the hundreds of thousands, not right at the all-time high."],
             ["Tiny pool, or +50% in the last 4 hours."]),
            ("How likely is a crash while you sleep?",
             "Three ways of estimating the chance of a crash (price falls 30% or more within the hours you are away).",
             ["<b>Our model</b>: under 15% = low, 15–35% = medium, over 35% = high.",
              "<b>Similar past moments</b>: \"8 of 30\" = of the 30 most similar moments in other coins, 8 crashed.",
              "<b>Past verdicts</b>: how often a crash followed this same verdict in tests.",
              "If the numbers disagree, trust the higher one and use a smaller amount."],
             ["Model under 15% and 4 or fewer of 30 similar moments crashed."],
             ["Model 35%+ or 10+ of 30 similar moments crashed."]),
            ("What pushes the risk up or down?",
             "The reasons behind the verdict.",
             ["❌ = strong danger sign (one is enough for AVOID).",
              "⚠️ = warning (two or more = CAUTION).",
              "⬆ = pushing the risk up · ⬇ = keeping the risk down."],
             ["\"No danger sign is active\" and more ⬇ than ⬆."],
             ["Any ❌, e.g. big holders selling or price falling fast."]),
            ("How far can the price move while you sleep?",
             "The lowest and highest price during your hours away, over many past nights.",
             ["\"-24% / +59%\" under <b>8 of 10 nights</b> = in 8 of 10 nights the price never went below -24% or "
              "above +59%.",
              "Your lower bound should be deeper than the drop in the <b>8 of 10</b> column — better, the <b>9 of 10</b> "
              "column."],
             ["Small numbers: a calm coin."],
             ["A drop of 40%+ in the 9 of 10 column: a wild coin; use a wide range or skip."]),
            ("Who holds the coin?",
             "The 10 biggest wallets (pools and contracts are not counted).",
             ["<b>Holds</b>: share of all coins.",
              "<b>Bought at (mcap)</b>: the market cap at their average buy price.",
              "<b>Profit now</b>: 7.7x = their coins are worth 7.7 times what they paid. Big profits = likely sellers. "
              "\"got it free\" = airdrop, claim or insider.",
              "<b>Last move</b> and <b>Change 24h</b>: a minus means they sold."],
             ["Top 10 hold under 20%, few big profits, nobody selling."],
             ["Top 10 hold over 25%, most of them up 3x+, and some sold in the last hours."]),
            ("Is money coming in or going out?",
             "All buys and sells in every pool, for the last 1, 4 and 24 hours.",
             ["<b>Net</b>: + = more buying, - = more selling.",
              "<b>Buyers / sellers</b>: how many different wallets.",
              "<b>Big wallets net</b>: what wallets that ever held 0.3%+ of all coins bought (+) or sold (-), as a share "
              "of all coins."],
             ["Net positive and big wallets net at 0 or positive."],
             ["Big wallets net -0.5% or worse in 4 hours (or -1% in 24 hours), or twice as much selling as buying in "
              "the last hour."]),
            ("Liquidity and price floors",
             "Where money sits in the pools around the price, and where buyers stepped in before.",
             ["The chart is vertical: up = higher price, the white line = the price now.",
              "<b>Green bars</b> (below the price) = money waiting to buy if the price drops. They slow a fall.",
              "<b>Purple bars</b> (above) = coins waiting to be sold if the price rises. They slow a pump.",
              "Longer bar = stronger wall. <b>Grey bars</b> (right) = where people traded in the last 7 days.",
              "Green-grey bands = price floors, purple bands = resistance. White dashed lines + the bright bar on the right "
              "edge = the safe range (single-sided: from the lower bound up to \"now\"). Orange dashed line = take profit "
              "(two-sided only).",
              "Floors table: <b>strong</b> = bounced 3+ times this week. <b>Selling needed to get there</b> = how many "
              "dollars someone has to sell to push the price to that floor."],
             ["Long green bars just below the price and a strong floor above your lower bound."],
             ["\"Almost no liquidity between …\" (the price can slide fast there), or one wallet able to push the price "
              "down 50%+."]),
            ("How deep should your range go? (single-sided USDG)",
             "For single-sided USDG: the closest lower bound the price did not fall to during the whole night in at least 90% "
             "of similar nights, then 5–10% deeper for safety. The upper bound is always 0% (today's price).",
             ["<b>Lower bound</b> tile: the number to type into Uniswap (price and market cap). <b>Upper bound</b> tile: "
              "today's price.",
              "<b>Never hits the bottom</b>: how often the price never fell to your lower bound during the night. <b>Hits the "
              "bottom</b> is the opposite: from there you hold only coin.",
              "<b>Earning fees</b>: share of the time the price was inside your range. The rest of the time it was above "
              "today's price (you just hold USDG).",
              "<b>Average</b> and <b>Bad night</b> (worst 1 in 10): results in dollars for your position, fees included.",
              "Table rows: ✓ Recommended, the closest safe lower bound without margin, the other margin, your usual -60 / -65 "
              "/ -70%, your plan, and the lower bound with the highest average.",
              "<b>Every lower bound from -20% to -90%</b> (click to open): the whole ladder, to see what going deeper or "
              "shallower does.",
              "How to choose: start with ✓ Recommended. Want fewer bottom hits? Pick a deeper row (e.g. -65%): fewer fees, "
              "but safer."],
             ["Hits the bottom 5% or less, average positive."],
             ["A yellow box saying the safe range lost money at today's fees (wait), or hits the bottom 10%+."]),
            ("Which LP range should you open? (two-sided)",
             "The safe range: the tightest range that stayed in range the whole night in at least 90% of similar "
             "nights, plus a 5–10% safety margin.",
             ["<b>Lower / upper bound</b> tiles: the numbers (price and market cap) to type into Uniswap.",
              "<b>Stays in range all night</b>: how often the price never left the range.",
              "<b>Touches the lower bound / Hits the bottom</b>: the risky one. Below your lower bound you hold only the "
              "coin and lose like a holder.",
              "<b>Average</b> and <b>Bad night</b> (worst 1 in 10): results in dollars for your position, fees included.",
              "Table rows: ✓ Recommended, the tightest range without margin, the 5% / 10% margins, your usual -60 / -65 / "
              "-70% lower bounds, your plan, the range with the highest average (often out of range) and full range.",
              "How to choose: start with ✓ Recommended. Want fewer bottom hits? Pick a deeper lower bound row (e.g. "
              "-65%): fewer fees, but safer.",
              "<b>Fee level</b> (\"% a day\"): what a full-range LP earns in this pool now. Higher is better."],
             ["Stays in range 90%+, hits the bottom 3% or less, average positive."],
             ["A yellow box saying the safe range lost money at today's fees (wait), or a negative average."]),
            ("When does your position exit? (single-sided USDG)",
             "With only USDG you hold no coin above today's price, so there is nothing to sell up there. The exit is automatic.",
             ["<b>Automatic exit at 0%</b>: if the price dips into your range and comes back to today's price, your LP sells "
              "the coin back into USDG. You end up 100% USDG with the fees on top.",
              "<b>Bounce may stall at</b>: a price floor inside your range. A floor the price fell through often turns into a "
              "ceiling on the way back. If a bounce stops there and you don't want to wait, close the LP by hand (you get part "
              "USDG, part coin).",
              "<b>Lower bound</b>: below it you hold only coin. Decide then: sell, or wait for a bounce back into the range.",
              "<b>Resistance above today's price</b> (click to open): only matters for a two-sided position."],
             ["Alarm on, and big holders not selling."],
             ["Big holders selling right now: a dip into your range is more likely tonight."]),
            ("Where should you take profit? (two-sided)",
             "Resistance above the price: levels where the price was pushed back before, where many holders get their money "
             "back, or where sellers wait in the pools — and the best one to take profit at.",
             ["<b>Take profit at</b>: the level to exit, just under the resistance zone (price and market cap).",
              "<b>Automatic exit</b>: set your LP's upper bound there. If the price climbs into the resistance, your LP sells "
              "the coin for USDG on the way up, even while you sleep.",
              "<b>Strength</b>: strong = 3+ old floors the price fell through (a broken floor often becomes a ceiling), or 2+ "
              "with many holders at break-even there. Only strong levels held more often than a random price in tests; how "
              "often a level pushed the price back before did not predict anything.",
              "<b>Turned the price back in tests</b>: how often levels like this held when the price reached them (strong: "
              "about 5 in 10, a random price about 4 in 10; then the price usually fell ~20%).",
              "<b>Chance to reach</b>: how often the price touched that level during a night like this.",
              "<b>Momentum now</b>: with strong momentum (RSI 70+ or volume 2x), strong levels still held about 4 in 10; "
              "medium and weak ones usually broke. Overbought signals mean \"likely to break through\", not \"sell now\".",
              "The table lists every resistance up to +300%; the next level is your target if the first one breaks.",
              "The floor line is the exit on the way down."],
             ["A strong level within reach, calm momentum, upper bound set just under it."],
             ["Strong momentum with only medium/weak levels ahead (they tend to break), or big holders selling."]),
            ("What happened after similar moments?",
             "The 30 past moments in other coins that look most like right now, and what happened next.",
             ["\"X of the 30 closest moments crashed\" within 8 hours.",
              "The table shows the lowest and highest price in the 8 hours after each moment."],
             ["4 or fewer of 30 crashed."],
             ["10 or more of 30 crashed."]),
            ("Does it look like a coin right before a crash?",
             "Warning signs this coin shows now, next to coins at their top right before they crashed.",
             ["✓ = the sign is showing.",
              "<b>Still missing</b> = the sign to watch. If it shows up too, get out."],
             ["Green box: \"Does not look like a coin before a crash\"."],
             ["Red box: \"Looks very much like …\"."]),
            ("Chart and on-chain indicators",
             "Classic chart indicators plus holder data.",
             ["Tag <b>predicts crashes</b> = it helped predict crashes in tests.",
              "Tag <b>info only</b> = it did not (RSI, moving averages). Don't open or skip because of these alone."],
             ["Few \"predicts crashes\" items pointing to risk."],
             ["Several \"predicts crashes\" items pointing to risk."]),
            ("Last 72 hours",
             "Three stacked charts.",
             ["Top: market cap.",
              "Middle: big holders selling (+) or buying (-). Above the red dashed line (1.5%) = danger.",
              "Bottom strip: the verdict this tool would have given at each moment (green OK, yellow CAUTION, red AVOID).",
              "Orange dashed lines on the top chart: the take-profit level (two-sided only) and the floor."],
             ["Mostly green strip, middle line below the dashed line."],
             ["Middle line above the dashed line right now."]),
            ("If you sleep … hours",
             "What your position is worth if the price falls 30% or 50% while you sleep.",
             ["Compares your range with the safe range, in dollars."],
             ["A loss you can live with."],
             ["A loss you can't afford: use less money."]),
            ("Alarm while the position is open",
             "A command that sends you a Telegram message when the verdict turns AVOID.",
             ["When it fires: wake up, check the coin, and pull your LP if needed."],
             ["Alarm running every night you have a position open."],
             ["No alarm on a coin rated CAUTION."]),
        ],
        "glossary_title": "Words used on the page",
        "glossary": [
            ("LP range", "The price band where your liquidity works and earns fees."),
            ("Lower / upper bound", "The bottom and top of your range, as % from today's price. Single-sided USDG: the upper "
                                    "bound is 0% (today's price)."),
            ("Single-sided USDG", "You add only USDG. The range runs from today's price down to your lower bound (e.g. 0% to "
                                  "-65%)."),
            ("Two-sided", "You add USDG and the coin. The range goes below and above today's price (e.g. -65% / +65%)."),
            ("Entry market cap", "The market cap when you opened your position (for single-sided USDG: the top of your "
                                 "range)."),
            ("Good exit", "Where to close an open position: just under the first strong resistance above the price, or the "
                          "top of your range if nothing strong is in the way."),
            ("Out of range (OOR)", "The price left your range. Below: you hold only the coin. Above: you hold only USDG "
                                   "(no loss, but no fees)."),
            ("Crash", "The price falls 30% or more within the hours you are away."),
            ("Market cap (mcap)", "Price × all coins."),
            ("ATH", "All-time high: the highest price ever."),
            ("Big holder / whale", "A wallet that ever held 0.3% or more of all coins."),
            ("Fee level", "How much a full-range LP earns per day in this pool right now (fee × trading ÷ pool size)."),
            ("Safety margin", "Extra room (5% or 10%) added on both sides of the tightest safe range."),
            ("Bad night (1 in 10)", "The result on the worst 1 of 10 similar nights."),
            ("Similar moments", "Past hours in other coins that looked most like right now."),
            ("Sample coins", "The 24 past coins the tool learned from."),
            ("Rewind", "See what the tool would have said at a past moment."),
            ("Resistance", "A price above where the price tends to stall or turn back down."),
            ("Momentum", "Strong buying pressure (RSI 70+ or volume 2x normal): it tends to push through weaker resistance."),
        ],
        "check_title": "Checklist before you open",
        "check": [
            "The top box is green (or teal, if you accept small fees).",
            "Crash risk is low, and 5 or fewer of 30 similar moments crashed.",
            "No ❌ danger sign.",
            "Big wallets are not selling (Money flow, Who holds the coin).",
            "You use the safe range, and \"Hits the bottom\" is 5% or less.",
            "The amount is money you can afford to lose.",
            "The alarm is on.",
        ],
        "limits_title": "Keep in mind",
        "limits": [
            "Nothing here is a guarantee. Coins can crash without warning.",
            "All numbers come from how 24 past coins behaved. A new kind of coin can behave differently.",
            "Liquidity is a snapshot (up to 15 minutes old). Prices are checked every 5 minutes.",
        ],
        "nudge": "New here? Open the <b>📖 How to read the results</b> tab at the top of the page.",
    },
    "id": {
        "lang": "Bahasa Indonesia",
        "intro": "Baca halaman dari atas. Kotak pertama adalah jawabannya; semua bagian di bawahnya menjelaskan "
                 "alasannya. Tidak perlu membaca semua bagian setiap kali.",
        "quick_title": "Cara cepat (30 detik)",
        "quick": [
            "Lihat <b>kotak berwarna paling atas</b> dan judulnya.",
            "Baca <b>What to do</b> di bagian bawah kotak itu. Isinya sudah menggabungkan semua analisa di halaman.",
            "Kalau buka LP: pakai <b>safe range</b> yang ditampilkan, pakai uang yang siap hilang, dan nyalakan "
            "<b>alarm</b>.",
            "Rencanakan exit: single-sided USDG keluar sendiri saat harga kembali ke harga hari ini. Two-sided: baris "
            "<b>Take profit</b> menunjukkan resistance tempat menjual (pasang batas atas tepat di bawahnya).",
            "Sudah punya posisi terbuka? Isi market cap saat kamu buka di <b>Already open?</b> (batas bawahmu di slider): "
            "kamu dapat satu jawaban (hold / ambil untung / tutup) dan tangga harga yang sederhana.",
        ],
        "pos_title": "Jenis posisimu — dan apa itu upper bound",
        "pos": [
            "<b>Upper bound</b> = batas atas range (harga tertinggi tempat likuiditasmu bekerja). <b>Lower bound</b> = batas "
            "bawah.",
            "<b>Single-sided USDG</b> (default): kamu hanya memasukkan USDG. Range-nya dari <b>harga sekarang (0%) turun ke "
            "batas bawahmu</b>, misalnya 0% sampai -65%. Jadi upper bound-nya ya 0% (harga saat buka) dan kamu hanya memilih "
            "batas bawah.",
            "Yang terjadi: kalau harga turun masuk range, USDG-mu membeli koin sedikit demi sedikit dan dapat fee. Di bawah "
            "batas bawah kamu hanya memegang koin dan berhenti dapat fee. Kalau harga <b>naik</b>, tidak terjadi apa-apa: kamu "
            "tetap 100% USDG (tidak rugi, tidak dapat fee).",
            "Exit-nya otomatis: kalau harga turun lalu kembali ke harga saat buka, LP menjual koin kembali jadi USDG dengan "
            "sendirinya.",
            "<b>Two-sided</b>: kamu memasukkan USDG dan koin, range-nya di bawah dan di atas harga sekarang (misalnya -65% / "
            "+65%). Di situ upper bound adalah tempat koinmu terjual, makanya bagian take profit menyarankan upper bound tepat "
            "di bawah resistance.",
            "Pilih jenisnya di <b>Position type</b> di atas form pada tab Check a coin.",
        ],
        "boxes_title": "4 kemungkinan jawaban",
        "boxes": [
            ("good", "✅ OK to open an LP", "Peluang crash rendah dan fee sepadan dengan risikonya. Buka safe range."),
            ("info", "✅ Low crash risk — but fees are too low", "Tidak berbahaya, tapi belum sepadan. Lebih baik tunggu."),
            ("warn", "⚠️ Risky — only small", "Peluang crash sedang atau ada tanda bahaya. Tunggu, atau sangat kecil."),
            ("bad", "🚫 Don't open an LP now", "Peluang tinggi (35%+) harga turun 30%+ saat kamu tidur."),
        ],
        "sections_title": "Per bagian (urutan sama dengan halaman)",
        "what": "Isinya", "how": "Cara membaca", "good": "Tanda bagus", "bad": "Tanda bahaya",
        "sections": [
            ("The answer box (top)",
             "Satu jawaban untuk saat ini: buka, tunggu, hati-hati, atau jangan buka — dengan 4 alasan singkat.",
             ["Warna dan judul adalah jawabannya (lihat 4 jawaban di atas).",
              "<b>Crash risk</b>: peluang dari model bahwa harga turun 30% atau lebih saat kamu tidur.",
              "<b>Safe range</b>: range yang dipakai, seberapa sering tetap di dalam range (single-sided: seberapa sering "
              "harga tidak pernah turun sampai batas bawah), dan hasil dalam dolar untuk ukuran posisimu.",
              "<b>Exit</b> (single-sided) atau <b>Take profit</b> (two-sided): bagaimana posisi ditutup.",
              "<b>Big holders</b> dan <b>Money flow</b>: siapa yang bisa menjual dan ke mana uang mengalir.",
              "<b>What to do</b>: tindakannya. Baca ini dulu."],
             ["Kotak hijau dengan \"Open the safe range …\"."],
             ["Kotak merah, atau kalimat \"similar past moments crashed more often than the model says\" (pakai jumlah "
              "lebih kecil)."]),
            ("Your position: when to exit",
             "Hanya muncul kalau kamu isi <b>Already open?</b>: satu jawaban untuk posisimu, lalu tangga harga.",
             ["<b>Kotak berwarna</b> adalah jawabannya: <b>Hold — it exits by itself at …</b> (batas atas range-mu, di situ LP "
              "kembali 100% USDG sendiri), <b>Take profit by hand at …</b> (ada resistance kuat di bawah batas atas range), "
              "<b>Nothing to do</b> (harga di atas range: kamu sudah 100% USDG) atau <b>Close it now</b> (vonis AVOID).",
              "<b>Tangga harga</b> dibaca seperti chart, dari atas ke bawah: ✅ batas atas range, 🎯 level ambil untung, 📍 "
              "sekarang, ⚠️ level tutup, 🔻 batas bawah range. Tiap baris: market cap, apa yang terjadi di situ, nilai LP-mu di "
              "situ, dan seberapa sering harga sampai ke sana dalam satu malam seperti ini.",
              "<b>Close if it falls below this</b>: floor kuat yang hanya tersentuh di maksimal 1 dari 4 malam — turun biasa "
              "tidak sampai ke situ, jadi kalau jebol, ada yang tidak beres.",
              "Angka dolar belum termasuk fee yang sudah didapat LP-mu (lihat di Uniswap).",
              "<b>Details: every level</b> (klik untuk membuka) berisi semua resistance."],
             ["\"Hold — it exits by itself\" dengan \"happens on\" tinggi, dan level tutup masih jauh."],
             ["\"Close it now\", atau harga sudah dekat level tutup."]),
            ("Coin tiles",
             "Empat angka tentang koin saat ini.",
             ["<b>Market cap</b>: harga × semua koin.",
              "<b>From all-time high</b>: -2% berarti tepat di puncak (lebih berisiko); -60% berarti jauh di bawah puncak.",
              "<b>Last 4 hours</b>: + besar berarti baru saja pump.",
              "<b>USDG pool size</b>: uang di pool tempat kamu LP. Makin besar = harga makin susah digerakkan."],
             ["Pool ratusan ribu dolar, tidak tepat di all-time high."],
             ["Pool kecil sekali, atau +50% dalam 4 jam terakhir."]),
            ("How likely is a crash while you sleep?",
             "Tiga cara memperkirakan peluang crash (harga turun 30% atau lebih selama jam kamu pergi).",
             ["<b>Our model</b>: di bawah 15% = rendah, 15–35% = sedang, di atas 35% = tinggi.",
              "<b>Similar past moments</b>: \"8 of 30\" = dari 30 momen paling mirip di koin lain, 8 crash.",
              "<b>Past verdicts</b>: seberapa sering crash terjadi setelah vonis yang sama saat diuji.",
              "Kalau angkanya berbeda, percayai yang lebih tinggi dan pakai jumlah lebih kecil."],
             ["Model di bawah 15% dan maksimal 4 dari 30 momen mirip yang crash."],
             ["Model 35%+ atau 10+ dari 30 momen mirip crash."]),
            ("What pushes the risk up or down?",
             "Alasan di balik vonis.",
             ["❌ = tanda bahaya kuat (satu saja cukup untuk AVOID).",
              "⚠️ = peringatan (dua atau lebih = CAUTION).",
              "⬆ = menaikkan risiko · ⬇ = menurunkan risiko."],
             ["\"No danger sign is active\" dan ⬇ lebih banyak dari ⬆."],
             ["Ada ❌, misalnya holder besar menjual atau harga jatuh cepat."]),
            ("How far can the price move while you sleep?",
             "Harga terendah dan tertinggi selama jam kamu pergi, dari banyak malam sebelumnya.",
             ["\"-24% / +59%\" di kolom <b>8 of 10 nights</b> = di 8 dari 10 malam harga tidak pernah turun lebih dari "
              "-24% atau naik lebih dari +59%.",
              "Batas bawahmu sebaiknya lebih dalam dari angka turun di kolom <b>8 of 10</b> — lebih aman lagi, kolom "
              "<b>9 of 10</b>."],
             ["Angka kecil: koin tenang."],
             ["Turun 40%+ di kolom 9 of 10: koin liar; pakai range lebar atau skip."]),
            ("Who holds the coin?",
             "10 wallet terbesar (pool dan kontrak tidak dihitung).",
             ["<b>Holds</b>: porsi dari semua koin.",
              "<b>Bought at (mcap)</b>: market cap saat harga beli rata-rata mereka.",
              "<b>Profit now</b>: 7.7x = koin mereka bernilai 7,7 kali harga belinya. Untung besar = kemungkinan menjual. "
              "\"got it free\" = airdrop, claim, atau orang dalam.",
              "<b>Last move</b> dan <b>Change 24h</b>: tanda minus berarti mereka menjual."],
             ["Top 10 memegang di bawah 20%, sedikit yang untung besar, tidak ada yang menjual."],
             ["Top 10 memegang lebih dari 25%, kebanyakan untung 3x+, dan ada yang menjual beberapa jam terakhir."]),
            ("Is money coming in or going out?",
             "Semua beli dan jual di semua pool, untuk 1, 4, dan 24 jam terakhir.",
             ["<b>Net</b>: + = lebih banyak beli, - = lebih banyak jual.",
              "<b>Buyers / sellers</b>: jumlah wallet berbeda yang beli / jual.",
              "<b>Big wallets net</b>: yang dibeli (+) atau dijual (-) wallet yang pernah memegang 0,3%+ semua koin, "
              "sebagai porsi dari semua koin."],
             ["Net positif dan big wallets net 0 atau positif."],
             ["Big wallets net -0,5% atau lebih buruk dalam 4 jam (atau -1% dalam 24 jam), atau jual dua kali lipat "
              "beli dalam 1 jam terakhir."]),
            ("Liquidity and price floors",
             "Di mana uang berada di pool sekitar harga sekarang, dan di mana pembeli pernah menahan harga.",
             ["Grafiknya vertikal: ke atas = harga lebih tinggi, garis putih = harga sekarang.",
              "<b>Batang hijau</b> (di bawah harga) = uang yang siap membeli kalau harga turun. Memperlambat penurunan.",
              "<b>Batang ungu</b> (di atas) = koin yang siap dijual kalau harga naik. Memperlambat pump.",
              "Batang makin panjang = tembok makin kuat. <b>Batang abu-abu</b> (kanan) = harga tempat orang bertransaksi "
              "7 hari terakhir.",
              "Pita hijau-abu = price floor (support), pita ungu = resistance. Garis putih putus-putus + batang terang di tepi "
              "kanan = safe range (single-sided: dari batas bawah sampai \"now\"). Garis oranye putus-putus = take profit "
              "(hanya two-sided).",
              "Tabel floor: <b>strong</b> = memantul 3+ kali minggu ini. <b>Selling needed to get there</b> = berapa "
              "dolar yang harus dijual untuk mendorong harga ke floor itu."],
             ["Batang hijau panjang tepat di bawah harga dan floor kuat di atas batas bawahmu."],
             ["\"Almost no liquidity between …\" (harga bisa meluncur cepat di situ), atau satu wallet bisa menurunkan "
              "harga 50%+."]),
            ("How deep should your range go? (single-sided USDG)",
             "Untuk single-sided USDG: batas bawah terdekat yang tidak tersentuh harga sepanjang malam di minimal 90% malam "
             "serupa, lalu 5–10% lebih dalam supaya aman. Batas atasnya selalu 0% (harga sekarang).",
             ["Kotak <b>Lower bound</b>: angka yang diisi di Uniswap (harga dan market cap). Kotak <b>Upper bound</b>: harga "
              "sekarang.",
              "<b>Never hits the bottom</b>: seberapa sering harga tidak pernah turun sampai batas bawah selama malam itu. "
              "<b>Hits the bottom</b> kebalikannya: dari situ kamu hanya memegang koin.",
              "<b>Earning fees</b>: porsi waktu harga berada di dalam range. Sisanya harga di atas harga sekarang (kamu hanya "
              "memegang USDG).",
              "<b>Average</b> dan <b>Bad night</b> (terburuk 1 dari 10): hasil dalam dolar untuk posisimu, sudah termasuk fee.",
              "Baris tabel: ✓ Recommended, batas bawah aman terdekat tanpa jarak, jarak yang lain, kebiasaanmu -60 / -65 / "
              "-70%, rencanamu, dan batas bawah dengan rata-rata tertinggi.",
              "<b>Every lower bound from -20% to -90%</b> (klik untuk membuka): semua batas bawah, untuk melihat efek lebih "
              "dalam atau lebih dangkal.",
              "Cara memilih: mulai dari ✓ Recommended. Mau lebih jarang kena bawah? Pilih baris yang lebih dalam (misalnya "
              "-65%): fee lebih kecil, tapi lebih aman."],
             ["Hits the bottom maksimal 5%, average positif."],
             ["Kotak kuning yang bilang safe range rugi di fee hari ini (tunggu), atau hits the bottom 10%+."]),
            ("Which LP range should you open? (two-sided)",
             "Safe range: range tersempit yang tetap di dalam range sepanjang malam di minimal 90% malam serupa, "
             "ditambah jarak aman 5–10%.",
             ["Kotak <b>Lower / upper bound</b>: angka (harga dan market cap) yang diisi di Uniswap.",
              "<b>Stays in range all night</b>: seberapa sering harga tidak pernah keluar range.",
              "<b>Touches the lower bound / Hits the bottom</b>: yang berbahaya. Di bawah batas bawah kamu hanya memegang "
              "koin dan rugi seperti holder biasa.",
              "<b>Average</b> dan <b>Bad night</b> (terburuk 1 dari 10): hasil dalam dolar untuk posisimu, sudah "
              "termasuk fee.",
              "Baris tabel: ✓ Recommended, range tersempit tanpa jarak, jarak 5% / 10%, batas bawah kebiasaanmu -60 / -65 "
              "/ -70%, rencanamu, range dengan rata-rata tertinggi (sering OOR), dan full range.",
              "Cara memilih: mulai dari ✓ Recommended. Mau lebih jarang kena bawah? Pilih baris batas bawah lebih dalam "
              "(misalnya -65%): fee lebih kecil, tapi lebih aman.",
              "<b>Fee level</b> (\"% a day\"): penghasilan LP full range di pool ini sekarang. Makin tinggi makin bagus."],
             ["Stays in range 90%+, hits the bottom maksimal 3%, average positif."],
             ["Kotak kuning yang bilang safe range rugi di fee hari ini (tunggu), atau average negatif."]),
            ("When does your position exit? (single-sided USDG)",
             "Dengan hanya USDG kamu tidak memegang koin di atas harga sekarang, jadi tidak ada yang dijual di atas. Exit-nya "
             "otomatis.",
             ["<b>Automatic exit at 0%</b>: kalau harga turun masuk range lalu kembali ke harga saat buka, LP menjual koin "
              "kembali jadi USDG. Kamu kembali 100% USDG plus fee.",
              "<b>Bounce may stall at</b>: price floor di dalam range-mu. Floor yang sudah ditembus harga sering berubah jadi "
              "atap saat harga memantul naik. Kalau pantulan tertahan di situ dan kamu tidak mau menunggu, tutup LP manual "
              "(dapat sebagian USDG, sebagian koin).",
              "<b>Lower bound</b>: di bawahnya kamu hanya memegang koin. Putuskan saat itu: jual, atau tunggu harga memantul "
              "masuk range lagi.",
              "<b>Resistance above today's price</b> (klik untuk membuka): hanya berpengaruh untuk posisi two-sided."],
             ["Alarm menyala, dan holder besar tidak menjual."],
             ["Holder besar sedang menjual: harga lebih mungkin turun masuk range-mu malam ini."]),
            ("Where should you take profit? (two-sided)",
             "Resistance di atas harga: level tempat harga pernah ditolak, tempat banyak holder balik modal, atau tempat penjual "
             "menunggu di pool — dan level terbaik untuk ambil untung.",
             ["<b>Take profit at</b>: level untuk keluar, tepat di bawah zona resistance (harga dan market cap).",
              "<b>Automatic exit</b>: pasang batas atas LP di situ. Kalau harga naik ke resistance, LP otomatis menjual koin "
              "jadi USDG saat harga naik, walau kamu sedang tidur.",
              "<b>Strength</b>: strong = 3+ floor lama yang sudah ditembus (floor yang jebol sering jadi atap), atau 2+ dengan "
              "banyak holder balik modal di situ. Hanya level strong yang lebih sering menahan daripada harga acak saat diuji; "
              "seberapa sering level itu dulu menolak harga ternyata tidak memprediksi apa-apa.",
              "<b>Turned the price back in tests</b>: seberapa sering level seperti ini menahan harga saat tersentuh (strong: "
              "sekitar 5 dari 10, harga acak sekitar 4 dari 10; sesudahnya harga biasanya turun ~20%).",
              "<b>Chance to reach</b>: seberapa sering harga menyentuh level itu dalam satu malam seperti ini.",
              "<b>Momentum now</b>: saat momentum kencang (RSI 70+ atau volume 2x), level strong masih menahan sekitar 4 dari "
              "10; medium dan weak biasanya tembus. Sinyal overbought artinya \"cenderung tembus\", bukan \"jual sekarang\".",
              "Tabel berisi semua resistance sampai +300%; level berikutnya jadi target kalau yang pertama tembus.",
              "Garis floor adalah titik keluar kalau harga turun."],
             ["Level strong yang terjangkau, momentum tenang, batas atas dipasang tepat di bawahnya."],
             ["Momentum kencang tapi di depan hanya level medium/weak (cenderung tembus), atau holder besar menjual."]),
            ("What happened after similar moments?",
             "30 momen di koin lain yang paling mirip dengan sekarang, dan apa yang terjadi sesudahnya.",
             ["\"X of the 30 closest moments crashed\" dalam 8 jam.",
              "Tabel menunjukkan harga terendah dan tertinggi 8 jam setelah tiap momen."],
             ["Maksimal 4 dari 30 yang crash."],
             ["10 atau lebih dari 30 crash."]),
            ("Does it look like a coin right before a crash?",
             "Tanda bahaya yang ada di koin ini sekarang, dibandingkan dengan koin lain di puncaknya tepat sebelum crash.",
             ["✓ = tanda itu sedang muncul.",
              "<b>Still missing</b> = tanda yang perlu diawasi. Kalau ikut muncul, keluar."],
             ["Kotak hijau: \"Does not look like a coin before a crash\"."],
             ["Kotak merah: \"Looks very much like …\"."]),
            ("Chart and on-chain indicators",
             "Indikator chart klasik plus data holder.",
             ["Label <b>predicts crashes</b> = terbukti membantu memprediksi crash saat diuji.",
              "Label <b>info only</b> = tidak terbukti (RSI, moving average). Jangan buka atau skip hanya karena ini."],
             ["Sedikit item \"predicts crashes\" yang menunjuk ke risiko."],
             ["Beberapa item \"predicts crashes\" menunjuk ke risiko."]),
            ("Last 72 hours",
             "Tiga grafik bertumpuk.",
             ["Atas: market cap.",
              "Tengah: holder besar menjual (+) atau membeli (-). Di atas garis merah putus-putus (1,5%) = bahaya.",
              "Strip bawah: vonis yang akan diberikan tool di tiap momen (hijau OK, kuning CAUTION, merah AVOID).",
              "Garis oranye putus-putus di grafik atas: level take profit (hanya two-sided) dan floor."],
             ["Strip kebanyakan hijau, garis tengah di bawah garis putus-putus."],
             ["Garis tengah di atas garis putus-putus saat ini."]),
            ("If you sleep … hours",
             "Nilai posisimu kalau harga turun 30% atau 50% saat kamu tidur.",
             ["Membandingkan range-mu dengan safe range, dalam dolar."],
             ["Kerugian yang masih bisa kamu terima."],
             ["Kerugian yang tidak sanggup kamu tanggung: kurangi jumlahnya."]),
            ("Alarm while the position is open",
             "Perintah yang mengirim pesan Telegram saat vonis berubah jadi AVOID.",
             ["Kalau berbunyi: bangun, cek koinnya, tarik LP kalau perlu."],
             ["Alarm jalan setiap malam saat ada posisi terbuka."],
             ["Tanpa alarm di koin dengan vonis CAUTION."]),
        ],
        "glossary_title": "Istilah di halaman",
        "glossary": [
            ("LP range", "Rentang harga tempat likuiditasmu bekerja dan mendapat fee."),
            ("Lower / upper bound", "Batas bawah dan atas range, dalam % dari harga sekarang. Single-sided USDG: batas atas "
                                    "= 0% (harga saat buka)."),
            ("Single-sided USDG", "Kamu hanya memasukkan USDG. Range dari harga sekarang turun ke batas bawah (misalnya 0% "
                                  "sampai -65%)."),
            ("Two-sided", "Kamu memasukkan USDG dan koin. Range di bawah dan di atas harga sekarang (misalnya -65% / +65%)."),
            ("Entry market cap", "Market cap saat kamu membuka posisi (untuk single-sided USDG: batas atas range-mu)."),
            ("Good exit", "Tempat menutup posisi yang sudah terbuka: tepat di bawah resistance kuat pertama di atas harga, atau "
                          "batas atas range kalau tidak ada yang kuat di depan."),
            ("Out of range (OOR)", "Harga keluar dari range. Di bawah: kamu hanya memegang koin. Di atas: kamu hanya "
                                   "memegang USDG (tidak rugi, tapi tidak dapat fee)."),
            ("Crash", "Harga turun 30% atau lebih selama jam kamu pergi."),
            ("Market cap (mcap)", "Harga × semua koin."),
            ("ATH", "All-time high: harga tertinggi sepanjang masa."),
            ("Big holder / whale", "Wallet yang pernah memegang 0,3% atau lebih dari semua koin."),
            ("Fee level", "Penghasilan per hari LP full range di pool ini sekarang (fee × volume ÷ ukuran pool)."),
            ("Safety margin", "Ruang tambahan (5% atau 10%) di kedua sisi range aman tersempit."),
            ("Bad night (1 in 10)", "Hasil di 1 malam terburuk dari 10 malam serupa."),
            ("Similar moments", "Jam-jam di koin lain yang paling mirip dengan kondisi sekarang."),
            ("Sample coins", "24 koin lama tempat tool ini belajar."),
            ("Rewind", "Lihat apa yang akan dikatakan tool di suatu waktu di masa lalu."),
            ("Resistance", "Harga di atas tempat harga cenderung tertahan atau berbalik turun."),
            ("Momentum", "Tekanan beli kuat (RSI 70+ atau volume 2x normal): cenderung menembus resistance yang lemah."),
        ],
        "check_title": "Checklist sebelum buka LP",
        "check": [
            "Kotak paling atas hijau (atau teal, kalau kamu terima fee kecil).",
            "Crash risk rendah, dan maksimal 5 dari 30 momen mirip yang crash.",
            "Tidak ada tanda bahaya ❌.",
            "Wallet besar tidak sedang menjual (Money flow, Who holds the coin).",
            "Pakai safe range, dan \"Hits the bottom\" maksimal 5%.",
            "Jumlahnya uang yang siap hilang.",
            "Alarm menyala.",
        ],
        "limits_title": "Perlu diingat",
        "limits": [
            "Tidak ada yang dijamin. Koin bisa crash tanpa tanda.",
            "Semua angka berasal dari perilaku 24 koin lama. Koin jenis baru bisa berperilaku berbeda.",
            "Likuiditas adalah potret (paling lama 15 menit). Harga dicek tiap 5 menit.",
        ],
        "nudge": "Baru pertama kali? Buka tab <b>📖 How to read the results</b> di bagian atas halaman.",
    },
}
