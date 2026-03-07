# PuratchiThaai — Deployment, Domain & SEO Guide

**Domain:** https://www.puratchithaai.org  
**Hosted on:** Render  
**App Type:** Flask (Python) — Membership ID Card Generator

---

## Table of Contents

1. [Render Deployment](#1-render-deployment)
2. [Custom Domain Setup](#2-custom-domain-setup)
3. [SEO Implemented](#3-seo-implemented)
4. [Google Search Console Setup](#4-google-search-console-setup)
5. [Google Analytics Setup](#5-google-analytics-setup)
6. [Submit to Search Engines](#6-submit-to-search-engines)
7. [Social Media Sharing](#7-social-media-sharing)
8. [Ongoing SEO Maintenance](#8-ongoing-seo-maintenance)
9. [Common Tasks / How-To](#9-common-tasks--how-to)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Render Deployment

### Already Deployed?
If already deployed, skip to Section 2. Otherwise:

### First Time Deploy
1. Go to [Render Dashboard](https://dashboard.render.com/)
2. Click **New** → **Web Service**
3. Connect your GitHub repo
4. Configure:
   - **Name:** `puratchithaai`
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app --bind 0.0.0.0:$PORT --workers 4`
5. Add **Environment Variables:**
   ```
   FLASK_ENV=production
   FLASK_SECRET=<your-secret-key>
   MONGODB_URI=<your-mongodb-atlas-uri>
   CLOUDINARY_URL=<your-cloudinary-url>
   REDIS_URL=<your-redis-url>
   ALLOWED_ORIGINS=https://www.puratchithaai.org,https://puratchithaai.org
   ```
6. Click **Deploy**

### Redeploy After Changes
- Push to GitHub → Render auto-deploys (if auto-deploy is ON)
- Or go to Render Dashboard → your service → **Manual Deploy** → **Deploy latest commit**

---

## 2. Custom Domain Setup

### On Render
1. Go to Render Dashboard → your web service → **Settings** → **Custom Domains**
2. Click **Add Custom Domain**
3. Add both:
   - `puratchithaai.org`
   - `www.puratchithaai.org`

### DNS Records (at your domain registrar)
Add these DNS records at your domain provider (GoDaddy/Namecheap/Cloudflare etc.):

| Type  | Name  | Value                          | TTL  |
|-------|-------|--------------------------------|------|
| CNAME | www   | `<your-app>.onrender.com`      | 3600 |
| CNAME | @     | `<your-app>.onrender.com`      | 3600 |

> **Note:** Some registrars don't allow CNAME on root (@). In that case:
> - Use **A record** pointing to Render's IP (check Render docs for current IPs)
> - Or use Cloudflare as DNS proxy (recommended)

### SSL/HTTPS
- Render provides **free SSL certificates** automatically
- After adding custom domain, wait 10-15 minutes for SSL to activate
- Verify: Visit `https://www.puratchithaai.org` — should show green lock

### Force WWW Redirect
Add a redirect rule in Render:
- From: `puratchithaai.org` → To: `https://www.puratchithaai.org` (301 redirect)

---

## 3. SEO Implemented

### ✅ What's Already Added

#### Meta Tags (in `templates/user/chatbot.html`)
- **Title tag** with Tamil + English keywords
- **Meta description** with key personalities and party info
- **Meta keywords** — comprehensive list including:
  - `puratchithaai`, `புரட்சித்தாய்`, `puratchi thaai`
  - `Sasikala`, `சசிகலா`, `Jayalalithaa`, `ஜெயலலிதா`
  - `Amma`, `அம்மா`, `MGR`, `எம்.ஜி.ஆர்`
  - `AIADMK`, `Tamil Nadu election 2026`, `Chennai politics`
  - `membership card generator`, `digital membership card`
  - `party membership registration`
- **Geo meta tags** — Chennai, Tamil Nadu coordinates
- **Language** — English + Tamil
- **Robots** — full indexing allowed on public pages

#### Open Graph (Facebook/WhatsApp)
- Full OG tags with absolute image URLs
- Image dimensions (1200x630) for optimal sharing
- Locale: `en_IN` with `ta_IN` alternate

#### Twitter Cards
- `summary_large_image` card type
- Full title, description, image

#### Structured Data (JSON-LD)
- **Organization** schema — name, logo, area served
- **WebApplication** schema — free app, features list
- **WebSite** schema — site name and description
- **BreadcrumbList** schema — navigation structure

#### Technical SEO
- `robots.txt` — allows crawling, blocks admin/api, points to sitemap
- `sitemap.xml` — lists all public pages
- **Canonical URLs** — prevents duplicate content
- **Hreflang tags** — English + Tamil language targeting
- **Admin pages** — `noindex, nofollow` to prevent indexing

---

## 4. Google Search Console Setup

### This is CRITICAL — Do this immediately!

1. Go to [Google Search Console](https://search.google.com/search-console/)
2. Click **Add Property**
3. Choose **URL prefix** → Enter `https://www.puratchithaai.org`
4. **Verify ownership** — Choose one method:

#### Method A: HTML Meta Tag (Easiest)
   - Google gives you a meta tag like:
     ```html
     <meta name="google-site-verification" content="YOUR_CODE_HERE">
     ```
   - Add it to `templates/user/chatbot.html` inside `<head>` after the charset meta tag
   - Deploy and verify

#### Method B: DNS TXT Record
   - Add a TXT record at your domain registrar:
     ```
     Type: TXT
     Name: @
     Value: google-site-verification=YOUR_CODE_HERE
     ```

### After Verification:
1. **Submit Sitemap:**
   - Go to **Sitemaps** in left menu
   - Enter: `sitemap.xml`
   - Click **Submit**

2. **Request Indexing:**
   - Go to **URL Inspection**
   - Enter: `https://www.puratchithaai.org/`
   - Click **Request Indexing**

3. **Check Coverage:**
   - Wait 2-7 days for Google to crawl
   - Check **Coverage** report for errors

---

## 5. Google Analytics Setup

1. Go to [Google Analytics](https://analytics.google.com/)
2. Create a **GA4 Property** for `puratchithaai.org`
3. Get your **Measurement ID** (starts with `G-`)
4. Add this script to `templates/user/chatbot.html` inside `<head>`:

```html
<!-- Google Analytics -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-XXXXXXXXXX"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-XXXXXXXXXX');
</script>
```

Replace `G-XXXXXXXXXX` with your actual Measurement ID.

> **Note:** When adding this, also update the Content-Security-Policy in `app.py` to allow `https://www.googletagmanager.com` and `https://www.google-analytics.com` in `script-src` and `connect-src`.

---

## 6. Submit to Search Engines

### Google
- ✅ Google Search Console (Section 4 above)
- Submit URL: https://www.google.com/ping?sitemap=https://www.puratchithaai.org/sitemap.xml

### Bing
1. Go to [Bing Webmaster Tools](https://www.bing.com/webmasters/)
2. Add your site → Verify → Submit sitemap
3. Or ping: https://www.bing.com/ping?sitemap=https://www.puratchithaai.org/sitemap.xml

### Yandex
1. Go to [Yandex Webmaster](https://webmaster.yandex.com/)
2. Add site → Verify → Submit sitemap

### Quick Ping All (run in browser or terminal):
```bash
curl "https://www.google.com/ping?sitemap=https://www.puratchithaai.org/sitemap.xml"
curl "https://www.bing.com/ping?sitemap=https://www.puratchithaai.org/sitemap.xml"
```

---

## 7. Social Media Sharing

### Test Your Share Cards
- **Facebook:** https://developers.facebook.com/tools/debug/ → Enter your URL
- **Twitter:** https://cards-dev.twitter.com/validator → Enter your URL
- **LinkedIn:** https://www.linkedin.com/post-inspector/ → Enter your URL

### Share for Backlinks & Traffic
Share the website on:
- [ ] WhatsApp groups (Tamil Nadu political groups)
- [ ] Facebook pages and groups
- [ ] Twitter/X with hashtags: `#PuratchiThaai #TamilNadu2026 #Sasikala #Amma #MGR`
- [ ] Instagram bio link
- [ ] YouTube description (if you have videos)
- [ ] Telegram groups

### Suggested Hashtags
```
#PuratchiThaai #புரட்சித்தாய் #TamilNadu2026 #TNPolitics
#Sasikala #சசிகலா #Jayalalithaa #ஜெயலலிதா #Amma #அம்மா
#MGR #AIADMK #Chennai #ChennaiPolitics #MembershipCard
#TamilNaduElection2026 #DravidianPolitics
```

---

## 8. Ongoing SEO Maintenance

### Weekly Tasks
- [ ] Check Google Search Console for errors
- [ ] Monitor search rankings for "puratchithaai"
- [ ] Share on social media for backlinks

### Monthly Tasks
- [ ] Update `sitemap.xml` lastmod date (in `app.py` → `sitemap_xml()` route)
- [ ] Check Google Analytics for traffic patterns
- [ ] Review and fix any crawl errors in Search Console
- [ ] Update meta description if party news/events change

### When You Add New Public Pages
1. Add the page URL to `sitemap.xml` in `app.py`
2. Add proper `<title>`, `<meta description>`, and OG tags
3. Resubmit sitemap in Google Search Console

---

## 9. Common Tasks / How-To

### How to Change the Website Title
Edit `templates/user/chatbot.html` → find the `<title>` tag → update text.

### How to Update Keywords
Edit `templates/user/chatbot.html` → find `<meta name="keywords"` → update the content.

### How to Update the Share Image (OG Image)
1. Create a new image (recommended: **1200x630 pixels**)
2. Replace `static/banner.jpg`
3. Clear Facebook cache: https://developers.facebook.com/tools/debug/

### How to Add Google Site Verification
Add inside `<head>` of `templates/user/chatbot.html`:
```html
<meta name="google-site-verification" content="YOUR_VERIFICATION_CODE">
```

### How to Add Google Analytics
See [Section 5](#5-google-analytics-setup) above.

### How to Add a New Page to Sitemap
Edit the `sitemap_xml()` function in `app.py` and add:
```xml
<url>
  <loc>https://www.puratchithaai.org/your-new-page</loc>
  <lastmod>2026-03-07</lastmod>
  <changefreq>monthly</changefreq>
  <priority>0.8</priority>
</url>
```

### How to Block a Page from Google
Add to that page's `<head>`:
```html
<meta name="robots" content="noindex, nofollow">
```

### How to Update Environment Variables on Render
1. Render Dashboard → your service → **Environment**
2. Add/edit the variable
3. Click **Save Changes** → service will auto-restart

### How to View Logs on Render
1. Render Dashboard → your service → **Logs**
2. Use filters for error/info levels

### How to Add a Custom Error Page
Create `templates/404.html` and add in `app.py`:
```python
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404
```

### How to Force Redeploy
Render Dashboard → your service → **Manual Deploy** → **Clear build cache & deploy**

### How to Scale Up (More Traffic)
Render Dashboard → your service → **Settings** → Change instance type or add more instances.

### How to Add Cloudflare CDN (Optional, Recommended)
1. Sign up at [Cloudflare](https://www.cloudflare.com/)
2. Add your domain `puratchithaai.org`
3. Change nameservers at your registrar to Cloudflare's
4. Enable: SSL Full (Strict), Auto Minify, Brotli compression
5. Set DNS records (CNAME to your Render app)
6. Benefits: CDN caching, DDoS protection, faster loading

---

## 10. Troubleshooting

### Site Not Showing on Google
- Did you submit to Google Search Console? (Section 4)
- Did you submit the sitemap?
- Wait 3-14 days for initial indexing
- Run: `site:puratchithaai.org` on Google to check

### SSL Certificate Error
- Wait 15-30 minutes after adding custom domain on Render
- Ensure DNS is pointing correctly
- Check Render dashboard for SSL status

### OG Image Not Showing on WhatsApp/Facebook
- Use absolute URLs: `https://www.puratchithaai.org/static/banner.jpg`
- Clear Facebook cache: https://developers.facebook.com/tools/debug/
- Image must be at least 200x200px, recommended 1200x630px

### Domain Not Resolving
- Check DNS propagation: https://dnschecker.org/#CNAME/www.puratchithaai.org
- DNS changes can take up to 48 hours
- Verify CNAME/A records are correct

### Changes Not Reflecting After Deploy
- Clear browser cache (Ctrl+Shift+R)
- Check Render deploy logs for errors
- Wait for deploy to complete (check status in dashboard)

---

## Quick Reference — File Locations

| What | File |
|------|------|
| Main page (SEO tags) | `templates/user/chatbot.html` |
| Card page | `templates/user/card.html` |
| Verify page | `templates/user/verify.html` |
| Admin template | `templates/base.html` |
| Routes (sitemap, robots) | `app.py` |
| Share image | `static/banner.jpg` |
| Logo | `static/name-logo.png` |
| Favicon | `static/favicon.jpg` |
| Dependencies | `requirements.txt` |
| Docker config | `Dockerfile`, `docker-compose.yml` |
| Render config | `Procfile` |

---

*Last updated: March 7, 2026*
