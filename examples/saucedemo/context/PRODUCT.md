# Swag Labs — Product Overview

## What is Swag Labs?

Swag Labs (a.k.a. "SauceDemo") is a demo e-commerce shop for Sauce Labs branded merchandise. A
shopper signs in with store credentials, browses the product catalogue, adds items to a cart and
checks out with a delivery address. It ships as part of the public Sauce Labs demo suite.

## Personas

**Shopper** — signs in, browses the catalogue, sorts products, opens product detail pages, adds
items to the cart, checks out and receives an order confirmation.

## Key Journeys

1. **Sign in** → the Products catalogue (`/inventory.html`)
2. **Browse catalogue** → sort by price → open product detail
3. **Checkout** → add item to cart → cart → checkout form → overview → "Thank you for your order!"
4. **Sign out** → back to the sign-in page

## Business Rules

| ID | Rule |
|----|------|
| R1 | Signing in with valid credentials opens the **Products** catalogue (`/inventory.html`). |
| R2 | Sorting the catalogue by **Price (low to high)** reorders items in ascending price order. |
| R3 | Adding a product to the cart **flips its button to "Remove"** and increments the cart badge. |
| R4 | The **cart lists the added item** with its **price** (e.g. "Sauce Labs Backpack" — $29.99). |
| R5 | The checkout form requires **First Name, Last Name and ZIP/Postal Code**; submitting without a value shows the field-specific inline error (e.g. "Error: Postal Code is required"). |
| R6 | The **checkout overview shows the item total, tax (Sauce Labs charges 8% US sales tax) and grand total**. |
| R7 | Placing the order opens the confirmation page ("Thank you for your order!" at `/checkout-complete.html`). |
| R8 | Each product **image renders on the inventory and detail pages** (distinct image per product). |
| R9 | Opening a product title link **navigates to that product's detail page** showing its name and description. |
| R10 | Signing out via the main menu **returns to the sign-in page** (`/`). |

## Catalogue (with canonical prices)

- Sauce Labs Backpack — **$29.99**
- Sauce Labs Bike Light — **$9.99**
- Sauce Labs Bolt T-Shirt — **$15.99**
- Sauce Labs Fleece Jacket — **$49.99**
- Sauce Labs Onesie — **$7.99**
- Test.allTheThings() T-Shirt (Red) — **$15.99**

## Checkout flow

Cart (`/cart.html`) → **Checkout** → Your Information (first name, last name, ZIP) → **Continue** →
Overview (item total, tax 8%, total) → **Finish** → Confirmation ("Thank you for your order!").

## Notes for automated agents

- The store requires sign-in before any other page; unauthenticated access to a deep link
  (e.g. `/inventory.html`) returns an HTTP **404** page with a "must be logged in" sad-face
  message (it does **not** redirect). Full-page reloads that the server answers also return
  status **404** even when authenticated, while still rendering the correct content — so a
  test must start on the sign-in page at `/` (HTTP 200) and sign in there.
- The cart header link is a client-side (non-href) navigation target; use the cart URL
  `/cart.html` directly.
- Displayed prices on the inventory page come from the live catalogue; the cart page re-prices items.