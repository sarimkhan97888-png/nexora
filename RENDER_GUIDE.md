# NEXORA: bilkul simple guide (free Render + free Neon database)

> Code likha hai par abhi live test nahi hua. Pehle Razorpay TEST mode aur apne test Discord server par try karo.
> Kisi ko apne tokens/keys mat dikhao. Kabhi GitHub par mat daalo.

## Step 0: Ye 6 free accounts banao
GitHub, Neon (database), Discord Developer Portal, Razorpay (payment), Render (hosting), UptimeRobot (optional, bot ko jagaye rakhta hai).

## Step 1: Code GitHub par daalo
1. github.com -> **New repository** -> naam `nexora` -> **Private** -> Create.
2. `nexora.zip` ko unzip karo. Us folder ke andar terminal kholo aur ek-ek line chalao:
```
git init
git add .
git status
git commit -m "first"
git branch -M main
git remote add origin https://github.com/TUMHARA-USERNAME/nexora.git
git push -u origin main
```
3. `git status` mein `.env` nahi dikhna chahiye. (Hum `.env` banate hi nahi; sab keys Render mein daalenge.)
Browser se bhi ho sakta hai: repo mein **Add file -> Upload files**, folder drag karo (ek baar mein max 100 files; zyada ho to 2 baar karo). Dhyan rahe `.gitignore` bhi upload ho.

## Step 2: Free database (Neon)
1. neon.tech -> sign up -> **Create project**.
2. Dashboard par **Connection string** dikhega. **"Pooled connection" ka toggle OFF karo.** Poora link copy karo
   (`postgresql://...neon.tech/neondb?sslmode=require`). Ye tumhara `DATABASE_URL` hai.

## Step 3: Discord bot
1. discord.com/developers/applications -> **New Application**.
2. General page: **Application ID** copy = `DISCORD_CLIENT_ID`.
3. **Bot** tab -> **Reset Token** -> copy = `DISCORD_BOT_TOKEN`. Neeche **Server Members Intent** aur **Message Content Intent** ON karo -> Save.
4. **OAuth2** tab -> **Client Secret** -> Reset -> copy = `DISCORD_CLIENT_SECRET`.
5. Apna Discord user ID: Discord Settings -> Advanced -> Developer Mode ON; apne naam par right click -> Copy User ID = `OWNER_DISCORD_IDS`.

## Step 4: Razorpay (payment)
1. razorpay.com -> sign up. Upar **Test Mode** ON rakho (pehle test).
2. Settings -> **API Keys** -> Generate. **Key Id** (`rzp_test_...`) = `RAZORPAY_KEY_ID`, **Key Secret** = `RAZORPAY_KEY_SECRET` (ek hi baar dikhta hai).
3. Webhook abhi mat banao (URL chahiye, Step 6 mein).
Dhyan: sirf ek fixed payment link se kaam nahi chalega, kyunki usse pata nahi chalta kis server ne pay kiya.
Bot har kharidari ke liye gateway se apna alag link banata hai; isiliye keys chahiye.
Live paisa lene ke liye Razorpay KYC/activation chahiye (kuch din lag sakte hain).

## Step 5: Render par chalao
1. render.com -> GitHub se login -> **New + -> Blueprint** -> `nexora` repo chuno. (Ya **Web Service** -> Docker -> Instance Type **Free**.)
2. Ye values bharo (poori list neeche table mein):
   `DATABASE_URL`, `DISCORD_BOT_TOKEN`, `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `OWNER_DISCORD_IDS`,
   `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` (koi bhi lamba random password khud banao, yaad rakho).
   `PUBLIC_BASE_URL` aur `DISCORD_OAUTH_REDIRECT_URI` abhi `https://temp.example.com` jaisa kuch daal do.
3. **Apply/Deploy**. 5-10 minute lagte hain. Tables apne aap ban jaate hain.
4. Deploy ke baad upar service ka URL milega, jaise `https://nexora-xxxx.onrender.com`. Ise copy karo.

## Step 6: URL sab jagah lagao
1. Render -> Environment: `PUBLIC_BASE_URL` = tumhara URL; `DISCORD_OAUTH_REDIRECT_URI` = `tumhara-URL/auth/callback`. Save (redeploy hoga).
2. Discord portal -> OAuth2 -> **Redirects** -> wahi `tumhara-URL/auth/callback` add karo -> Save.
3. Razorpay -> Settings -> **Webhooks -> Add new webhook**:
   - URL: `tumhara-URL/api/v1/webhooks/razorpay`
   - Secret: wahi jo `RAZORPAY_WEBHOOK_SECRET` mein daala
   - Events tick karo: `payment_link.paid`, `payment_link.expired`, `payment_link.cancelled`, `refund.processed`, `payment.dispute.created`
4. Browser mein `tumhara-URL/healthz` kholo: `{"status":"ok"}` dikhna chahiye.

## Step 7: Bot ko jagaye rakho (free plan sota hai)
uptimerobot.com -> Add monitor -> HTTP(s) -> URL `tumhara-URL/healthz` -> har 5 minute. (App khud bhi ping karti hai.)

## Step 8: Test karo
1. Bot ko apne test server mein add karo: website kholo -> Sign in with Discord -> "Add bot to this server".
2. Discord mein: `/setup`, `/plan`, `/buy` (7 days) -> link khulega -> Razorpay **test** payment karo (Razorpay docs mein test UPI/card details hain).
3. 1 minute baad `/plan` -> status **active** dikhna chahiye. Dashboard mein Payment history bhi dikhegi.
4. Razorpay webhook log mein same event "Resend" karo: access dobara extend **nahi** hona chahiye.
5. Sab sahi ho to `python scripts/smoke_test.py --base-url tumhara-URL` apne computer se chalao.

## Step 9: Asli paisa (jab sab test ho jaye)
Razorpay Live mode ON -> naye Live keys (`rzp_live_...`) -> Render mein `RAZORPAY_KEY_ID/SECRET` badlo -> Live mode mein **naya webhook** banao
(test aur live ke webhook alag hote hain) -> redeploy.

## Env variables ki poori list
| Naam | Kahan se |
|---|---|
| DATABASE_URL | Neon (pooled OFF) |
| DISCORD_BOT_TOKEN / CLIENT_ID / CLIENT_SECRET | Discord portal |
| DISCORD_OAUTH_REDIRECT_URI | `tumhara-URL/auth/callback` |
| PUBLIC_BASE_URL | Render ka URL |
| OWNER_DISCORD_IDS | tumhara Discord user ID |
| RAZORPAY_KEY_ID / KEY_SECRET | Razorpay API Keys |
| RAZORPAY_WEBHOOK_SECRET | tum khud banao, Razorpay webhook mein wahi daalo |
| PRICES | pehle se set: 7 din Rs99, 30 din Rs299, 365 din Rs1999 (paise mein: 9900, 29900, 199900) |
| SESSION_SECRET | Render khud banata hai |
| AI_BASE_URL, AI_MODEL, AI_API_KEY | sirf AI chalana ho to; warna khali |
| SENTRY_DSN | optional |

## Free plan ki sachchi limits
- Render free service 15 min traffic na aaye to so jaati hai; UptimeRobot + self-ping isse kam karte hain par poori guarantee nahi. Restart par bot 1-2 min offline ho sakta hai.
- Free hosting pe file save nahi rehti; ticket transcripts staff log channel mein bheje jaate hain.
- Payment webhook ke waqt site so rahi ho to Razorpay baad mein dobara bhejta hai; tab bhi access aa jaata hai.
- Asli customers ke liye baad mein paid plan lena behtar hai.

## Kuch gadbad ho to
Render -> Logs dekho. Error mujhe bhejo (token/keys hata ke).
