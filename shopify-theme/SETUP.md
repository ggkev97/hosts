# Store Setup Guide

## Pre-launch mode (recommended for Harasniplah's current stage)

While the store is password-protected (the default before picking a plan), visitors see the
branded splash in `templates/password.liquid`: brand story, "Get Early Access" email capture,
and a hidden password entry. That page IS the hype site — you can run it as-is for months,
collecting waitlist emails, before ever opening the store.

- Waitlist emails land in **Customers** in Shopify admin, tagged `newsletter, prelaunch`.
- Tag teaser products with `coming-soon` (in the product's Tags field) to show a crimson
  "Coming Soon" badge, `$XX.XX` price, and a "Notify Me" button instead of Add to cart.
- Set the hero's drop date in the theme editor to show the countdown.
- Build the Lookbook: create a page with template `page.lookbook`, then add photos to the
  Lookbook section in the theme editor.

A step-by-step guide for launching the clothing brand on Shopify with this custom theme ("Wardrobe").

## 1. Create the Shopify store

1. Go to [shopify.com](https://www.shopify.com) and click **Start free trial** (no credit card needed to start).
2. Sign up with an email, answer the onboarding questions (you can skip most), and pick a store name — this becomes your temporary `your-store.myshopify.com` URL.
3. You'll land in the **Shopify admin**. Everything below happens there.

> Pricing note: after the trial you'll need a plan. **Basic** is the usual choice for a new brand. The store stays password-protected until you pick a plan and remove the password.

## 2. Upload this theme

**Option A — zip upload (no tools needed):**

1. Zip the *contents* of this `shopify-theme/` folder (the zip should contain `layout/`, `sections/`, `templates/`, etc. at its top level — not a nested folder). On Mac: select the folders inside `shopify-theme`, right-click → Compress.
2. In Shopify admin: **Online Store → Themes → Add theme → Upload zip file**.
3. Once uploaded, click **Customize** to preview, then **Publish** when ready.

**Option B — Shopify CLI (for developers):**

```bash
npm install -g @shopify/cli
cd shopify-theme
shopify theme dev --store your-store.myshopify.com   # live preview
shopify theme push --store your-store.myshopify.com  # upload
```

## 3. Add products

1. **Products → Add product** in admin.
2. For each garment: title, description, photos (square or 3:4 portrait shots look best with this theme), price.
3. Add **variants** for sizes/colors (e.g. option "Size" with S/M/L/XL) — the theme renders these as swatch buttons automatically.
4. Set inventory quantities per variant so sold-out sizes show correctly.

## 4. Create collections and menus

1. **Products → Collections → Create collection** — e.g. "New arrivals", "Tees", "Hoodies". Collections power the homepage featured grid and the shop pages.
2. **Online Store → Navigation**: edit the **Main menu** (header links, e.g. Shop / About) and the **Footer menu** (Contact, policies).

## 5. Customize the theme

In **Online Store → Themes → Customize**:

- **Theme settings (bottom left)**: brand colors, fonts, logo, social links — the whole theme restyles from these.
- **Homepage**: click each section (Hero, Featured collection, Image with text, Newsletter) to swap images/text, or add/reorder/remove sections.
- **Header**: pick the menu; **Footer**: newsletter toggle and brand text.

## 6. Payments, shipping, legal

1. **Settings → Payments**: activate **Shopify Payments** (cards, Apple/Google Pay) — needs business/bank details. PayPal can be added alongside.
2. **Settings → Shipping and delivery**: set shipping rates (flat rate is simplest to start).
3. **Settings → Policies**: generate refund/privacy/terms templates and link them in the footer menu.
4. **Settings → Taxes and duties**: usually fine on defaults; check local requirements.

## 7. Domain

1. **Settings → Domains**: buy a domain through Shopify (easiest) or connect one from another registrar (Shopify shows the DNS records to set).
2. Set the custom domain as primary; the `.myshopify.com` URL redirects automatically.

## 8. Launch checklist

- [ ] Test a full order with Shopify's [test payment mode](https://help.shopify.com/en/manual/checkout-settings/test-orders) (or a real card + refund)
- [ ] Check the site on a phone
- [ ] Product photos on every product, alt text filled in
- [ ] Shipping rates cover every region you sell to
- [ ] Policies linked in footer
- [ ] Pick a plan, then **Online Store → Preferences → remove password protection**

## Theme file map (for future edits)

| Path | What it controls |
| --- | --- |
| `layout/theme.liquid` | Global HTML shell, fonts, CSS variables |
| `sections/` | Header, footer, hero, product/collection pages — each editable in the theme editor |
| `snippets/` | Reusable pieces: product card, price, cart drawer |
| `assets/base.css` | All styling (driven by CSS variables from theme settings) |
| `assets/theme.js` | Cart drawer, variant picker, mobile menu |
| `config/settings_schema.json` | What appears under "Theme settings" in the editor |
