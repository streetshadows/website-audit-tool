#!/usr/bin/env python3
"""Standards & best-practice knowledge base for the Website Audit Toolkit.

Single source of truth that maps each audit check to (a) the authoritative
standard/spec that defines it, (b) the governance/compliance frameworks it rolls
up to, and (c) a plain-English rationale for WHY a result passes or fails. The
check scripts attach these citations to their JSON/txt output and the report
quotes them, so every finding both *tests* and *teaches*.

Framework keys vary by domain (you cite the framework that actually governs the
control): security/TLS/DNS/email findings carry NIST CSF 2.0 / ISO 27001:2022 /
CIS Controls v8 / ACSC Essential Eight; accessibility carries WCAG 2.2 + EN
301 549 / Section 508 / ADA; SEO carries Google Search Essentials; performance
carries Google Core Web Vitals. Where a framework genuinely does not govern a
control (e.g. Essential Eight is endpoint-focused and does not cover web
response headers) we say so rather than forcing a bad mapping — that honesty is
itself part of the education.

CLI (independently testable / educational):
    python3 standards.py                 # dump the whole KB as JSON
    python3 standards.py --list          # one line per check id
    python3 standards.py seo-canonical hsts color-contrast   # cite specific checks
"""

import json
import sys

# Each entry:
#   title      short human name of the check
#   standard   the authoritative spec/clause that defines the requirement
#   url        canonical reference to read more
#   frameworks {framework: control/clause} governance roll-up (domain-appropriate)
#   why_fail   what a FAIL means + the best-practice remediation
#   why_pass   what a PASS confirms (so a green result is explained, not silent)
STANDARDS = {
    # ---------------------------------------------------------------- SEO ----
    "seo-title": {
        "title": "Page <title>",
        "standard": "Google Search Essentials — Title links & 'Influencing your title links'",
        "url": "https://developers.google.com/search/docs/appearance/title-link",
        "frameworks": {"Google Search Essentials": "Title links", "Schema.org": "n/a"},
        "why_fail": "A missing or off-length title (<10 or >65 chars) is truncated or "
        "auto-rewritten by Google, losing the strongest on-page relevance "
        "signal. Write one unique, descriptive 10-65 char title per page.",
        "why_pass": "A unique, descriptive title in the 10-65 char band renders intact in "
        "the SERP and gives Google a clear primary relevance signal.",
    },
    "seo-meta-description": {
        "title": "Meta description",
        "standard": "Google Search Essentials — Snippets / meta description best practice",
        "url": "https://developers.google.com/search/docs/appearance/snippet",
        "frameworks": {"Google Search Essentials": "Snippet"},
        "why_fail": "No / poorly-sized description (aim 50-165 chars) lets Google generate "
        "the snippet from arbitrary page text, hurting click-through. Write a "
        "unique summary per page.",
        "why_pass": "A 50-165 char unique description gives Google a strong snippet "
        "candidate, improving SERP click-through.",
    },
    "seo-canonical": {
        "title": "Canonical link",
        "standard": "RFC 6596 (rel=canonical) + Google canonicalization guidance",
        "url": "https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls",
        "frameworks": {"Google Search Essentials": "Canonicalization", "IETF": "RFC 6596"},
        "why_fail": "Without a self-referencing canonical, duplicate/parameterised URLs "
        "split ranking signals and Google may index the wrong variant. Add "
        "<link rel=canonical> pointing to the preferred URL.",
        "why_pass": "A self-referencing canonical consolidates duplicate URLs onto one "
        "indexable address and concentrates ranking signals.",
    },
    "seo-h1": {
        "title": "Exactly one <h1>",
        "standard": "WHATWG HTML — heading content / document outline",
        "url": "https://html.spec.whatwg.org/multipage/sections.html#headings-and-sections",
        "frameworks": {"WHATWG HTML": "Headings", "WCAG 2.2": "SC 1.3.1 Info & Relationships"},
        "why_fail": "Zero or multiple H1s muddy the document outline for crawlers and "
        "assistive tech. Use exactly one H1 describing the page's main topic.",
        "why_pass": "A single H1 gives a clear top-level topic for both search engines and screen-reader navigation.",
    },
    "seo-lang": {
        "title": "html lang attribute",
        "standard": "WCAG 2.2 SC 3.1.1 Language of Page (Level A)",
        "url": "https://www.w3.org/WAI/WCAG22/Understanding/language-of-page.html",
        "frameworks": {"WCAG 2.2": "SC 3.1.1 (A)", "EN 301 549": "9.3.1.1", "Section 508": "1194.22"},
        "why_fail": "Missing <html lang> means screen readers may use the wrong "
        "pronunciation engine and translation tools mis-detect language. Set "
        'lang (e.g. lang="en").',
        "why_pass": "A declared page language lets assistive tech pick the correct voice "
        "and lets browsers offer accurate translation.",
    },
    "seo-viewport": {
        "title": "Viewport meta (mobile)",
        "standard": "Google Search — Mobile-friendly / responsive design",
        "url": "https://developers.google.com/search/docs/crawling-indexing/mobile/mobile-sites-mobile-first-indexing",
        "frameworks": {"Google Search Essentials": "Mobile-first indexing"},
        "why_fail": "No <meta name=viewport> breaks responsive scaling on phones; with "
        "mobile-first indexing this directly harms ranking. Add "
        "width=device-width, initial-scale=1.",
        "why_pass": "A viewport meta enables responsive layout — required for mobile-first "
        "indexing and usable mobile UX.",
    },
    "seo-open-graph": {
        "title": "Open Graph / social tags",
        "standard": "Open Graph protocol (ogp.me)",
        "url": "https://ogp.me/",
        "frameworks": {"Open Graph": "og:title/description/image"},
        "why_fail": "Without og: tags, social/chat shares show no title, summary or "
        "preview image, lowering shared-link engagement. Add og:title, "
        "og:description, og:image.",
        "why_pass": "Open Graph tags give shared links a rich title/summary/image card, "
        "improving click-through from social and chat.",
    },
    "seo-jsonld": {
        "title": "Structured data (JSON-LD)",
        "standard": "Schema.org + Google structured-data guidelines",
        "url": "https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data",
        "frameworks": {"Schema.org": "vocabulary", "Google Search Essentials": "Structured data"},
        "why_fail": "No JSON-LD means no eligibility for rich results (Organization, "
        "Breadcrumb, FAQ, etc.) and weaker entity understanding. Add valid "
        "schema.org JSON-LD.",
        "why_pass": "Valid JSON-LD makes the page eligible for rich results and helps "
        "search engines model the site's entities.",
    },
    "seo-favicon": {
        "title": "Favicon",
        "standard": "Google Search — favicon guidelines",
        "url": "https://developers.google.com/search/docs/appearance/favicon-in-search",
        "frameworks": {"Google Search Essentials": "Favicon"},
        "why_fail": "No favicon link element means no brand icon in tabs, bookmarks or "
        "mobile SERPs. Add <link rel=icon>.",
        "why_pass": "A declared favicon shows brand identity in tabs/bookmarks and the mobile search result.",
    },
    "seo-thin-content": {
        "title": "Thin content",
        "standard": "Google Search Essentials — Helpful, people-first content",
        "url": "https://developers.google.com/search/docs/fundamentals/creating-helpful-content",
        "frameworks": {"Google Search Essentials": "Helpful content"},
        "why_fail": "Pages under ~200 words rarely demonstrate enough topical depth to "
        "rank and can trigger 'thin content' assessments. Expand with genuinely "
        "useful, original content.",
        "why_pass": "Substantive content gives the page enough signal to demonstrate "
        "topical relevance and helpfulness.",
    },
    "seo-img-alt": {
        "title": "Image alt text",
        "standard": "WCAG 2.2 SC 1.1.1 Non-text Content (Level A)",
        "url": "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content.html",
        "frameworks": {"WCAG 2.2": "SC 1.1.1 (A)", "EN 301 549": "9.1.1.1", "Section 508": "1194.22(a)"},
        "why_fail": "Images without alt are invisible to screen readers and to image "
        'search. Add descriptive alt (or alt="" for purely decorative images).',
        "why_pass": "Every content image has a text alternative — usable by assistive tech "
        "and indexable by image search.",
    },
    "seo-noindex": {
        "title": "Meta robots noindex",
        "standard": "Google — robots meta tag / noindex",
        "url": "https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag",
        "frameworks": {"Google Search Essentials": "robots meta"},
        "why_fail": "A noindex directive removes the page from search entirely — critical "
        "if unintended on a page meant to rank. Remove noindex from pages you "
        "want indexed.",
        "why_pass": "No unintended noindex — the page is allowed into the search index.",
    },
    "seo-duplicate-title": {
        "title": "Duplicate titles",
        "standard": "Google — duplicate/boilerplate titles",
        "url": "https://developers.google.com/search/docs/appearance/title-link",
        "frameworks": {"Google Search Essentials": "Title links"},
        "why_fail": "Identical titles across pages cause keyword cannibalisation and make "
        "SERP results indistinguishable. Make each title unique.",
        "why_pass": "Titles are unique per page, so each ranks for its own topic without cannibalisation.",
    },
    "seo-duplicate-description": {
        "title": "Duplicate descriptions",
        "standard": "Google — meta description best practice",
        "url": "https://developers.google.com/search/docs/appearance/snippet",
        "frameworks": {"Google Search Essentials": "Snippet"},
        "why_fail": "Repeated descriptions waste the snippet opportunity and signal "
        "templated content. Write a distinct description per page.",
        "why_pass": "Each page has a distinct description, maximising unique snippet value.",
    },
    "seo-broken-links": {
        "title": "Broken links (4xx/5xx)",
        "standard": "Google Search Essentials — site maintenance / crawlability",
        "url": "https://developers.google.com/search/docs/crawling-indexing/large-site-managing-crawl-budget",
        "frameworks": {"Google Search Essentials": "Crawl budget", "WCAG 2.2": "SC 2.4.4 Link Purpose"},
        "why_fail": "Links returning 4xx/5xx waste crawl budget, dead-end users and leak "
        "link equity. Fix or remove broken links; 301 moved targets.",
        "why_pass": "Internal/external links resolve, preserving crawl budget, UX and link equity.",
    },
    "seo-mixed-content": {
        "title": "Mixed content (http on https)",
        "standard": "W3C Mixed Content + Google HTTPS-as-ranking-signal",
        "url": "https://www.w3.org/TR/mixed-content/",
        "frameworks": {
            "W3C": "Mixed Content",
            "NIST CSF 2.0": "PR.DS-02 (data-in-transit)",
            "OWASP": "Transport security",
        },
        "why_fail": "http subresources on an https page are blocked or downgrade the lock "
        "icon, breaking assets and trust. Serve all subresources over https.",
        "why_pass": "All subresources load over https — no downgrade, no blocked assets, padlock intact.",
    },
    "seo-indexability": {
        "title": "Google indexability verdict",
        "standard": "Google — crawling, indexing & canonicalization",
        "url": "https://developers.google.com/search/docs/crawling-indexing/overview-google-crawlers",
        "frameworks": {"Google Search Essentials": "Indexing"},
        "why_fail": "A page is non-indexable when HTTP status != 200, meta/X-Robots-Tag "
        "noindex, robots.txt Disallow, or a cross-site canonical applies — it "
        "will not appear in Google. Confirm each is intentional.",
        "why_pass": "Status 200, no noindex, not robots-blocked, self-canonical — the page "
        "is eligible for the Google index.",
    },
    # -------------------------------------------------------- ACCESSIBILITY --
    # axe rule ids resolve to these via AXE_WCAG below; the most common are listed.
    "color-contrast": {
        "title": "Text colour contrast",
        "standard": "WCAG 2.2 SC 1.4.3 Contrast (Minimum) (Level AA)",
        "url": "https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html",
        "frameworks": {
            "WCAG 2.2": "SC 1.4.3 (AA)",
            "EN 301 549": "9.1.4.3",
            "Section 508": "1194.22",
            "ADA": "Title III effective-comms",
        },
        "why_fail": "Text below 4.5:1 (3:1 for large/bold ≥18.66px) is unreadable for low "
        "vision and in glare. Darken text or lighten background to meet the "
        "ratio.",
        "why_pass": "Text meets the 4.5:1 (or 3:1 large) ratio — legible for low-vision users and in poor lighting.",
    },
    "image-alt": {
        "title": "Image alternative text",
        "standard": "WCAG 2.2 SC 1.1.1 Non-text Content (Level A)",
        "url": "https://www.w3.org/WAI/WCAG22/Understanding/non-text-content.html",
        "frameworks": {"WCAG 2.2": "SC 1.1.1 (A)", "EN 301 549": "9.1.1.1", "Section 508": "1194.22(a)"},
        "why_fail": "<img> without alt gives screen-reader users no information. Add "
        'descriptive alt, or alt="" if decorative.',
        "why_pass": "Images expose a text alternative to assistive technology.",
    },
    "link-name": {
        "title": "Discernible link text",
        "standard": "WCAG 2.2 SC 2.4.4 Link Purpose / SC 4.1.2 Name, Role, Value",
        "url": "https://www.w3.org/WAI/WCAG22/Understanding/link-purpose-in-context.html",
        "frameworks": {"WCAG 2.2": "SC 2.4.4 (A) / 4.1.2 (A)", "EN 301 549": "9.2.4.4"},
        "why_fail": "Links with no accessible name (icon-only, empty) are announced as "
        "'link' with no destination. Provide text or aria-label.",
        "why_pass": "Every link has a discernible name conveying its destination.",
    },
    "button-name": {
        "title": "Accessible button name",
        "standard": "WCAG 2.2 SC 4.1.2 Name, Role, Value (Level A)",
        "url": "https://www.w3.org/WAI/WCAG22/Understanding/name-role-value.html",
        "frameworks": {"WCAG 2.2": "SC 4.1.2 (A)", "EN 301 549": "9.4.1.2"},
        "why_fail": "Buttons without a name (icon-only) are unusable by screen-reader / "
        "voice-control users. Add visible text or aria-label.",
        "why_pass": "Controls expose a programmatic name, usable by AT and voice control.",
    },
    "label": {
        "title": "Form field labels",
        "standard": "WCAG 2.2 SC 1.3.1 / 3.3.2 / 4.1.2",
        "url": "https://www.w3.org/WAI/WCAG22/Understanding/labels-or-instructions.html",
        "frameworks": {"WCAG 2.2": "SC 3.3.2 (A) / 4.1.2 (A)", "EN 301 549": "9.3.3.2"},
        "why_fail": "Inputs without an associated <label> can't be identified by AT. "
        "Associate a label (for/id) with every field.",
        "why_pass": "Every form field has a programmatically-associated label.",
    },
    "html-has-lang": {
        "title": "Document language",
        "standard": "WCAG 2.2 SC 3.1.1 Language of Page (Level A)",
        "url": "https://www.w3.org/WAI/WCAG22/Understanding/language-of-page.html",
        "frameworks": {"WCAG 2.2": "SC 3.1.1 (A)", "EN 301 549": "9.3.1.1"},
        "why_fail": "No lang on <html> breaks screen-reader pronunciation. Set the page language.",
        "why_pass": "Page language is declared for correct AT pronunciation.",
    },
    "document-title": {
        "title": "Page has a title",
        "standard": "WCAG 2.2 SC 2.4.2 Page Titled (Level A)",
        "url": "https://www.w3.org/WAI/WCAG22/Understanding/page-titled.html",
        "frameworks": {"WCAG 2.2": "SC 2.4.2 (A)", "EN 301 549": "9.2.4.2"},
        "why_fail": "A missing/empty <title> leaves users without orientation across tabs "
        "and history. Give each page a descriptive title.",
        "why_pass": "A descriptive document title orients users in tabs, history and AT.",
    },
    "landmark-regions": {
        "title": "Landmark regions / page structure",
        "standard": "WCAG 2.2 SC 1.3.1 Info & Relationships + WAI-ARIA Authoring Practices (landmarks)",
        "url": "https://www.w3.org/WAI/ARIA/apg/practices/landmark-regions/",
        "frameworks": {"WCAG 2.2": "SC 1.3.1 (A)", "WAI-ARIA": "APG Landmarks", "EN 301 549": "9.1.3.1"},
        "why_fail": "Content outside landmark regions (no <main>, multiple/duplicate "
        "landmarks) makes it hard for screen-reader users to navigate by region. "
        "Wrap content in semantic landmarks with exactly one <main>.",
        "why_pass": "Content sits inside unique, correct landmarks, enabling region-based screen-reader navigation.",
    },
    "bypass": {
        "title": "Bypass blocks / skip link",
        "standard": "WCAG 2.2 SC 2.4.1 Bypass Blocks (Level A)",
        "url": "https://www.w3.org/WAI/WCAG22/Understanding/bypass-blocks.html",
        "frameworks": {"WCAG 2.2": "SC 2.4.1 (A)", "EN 301 549": "9.2.4.1", "Section 508": "1194.22(o)"},
        "why_fail": "With no skip link or landmarks, keyboard users must tab through the "
        "nav on every page. Add a 'skip to main content' link and/or landmarks.",
        "why_pass": "A bypass mechanism (skip link/landmarks) lets keyboard users jump straight to main content.",
    },
    "a11y-contrast-independent": {
        "title": "Independent contrast cross-check (WCAG luminance)",
        "standard": "WCAG 2.2 SC 1.4.3 — relative-luminance formula (verified independently of axe)",
        "url": "https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html",
        "frameworks": {"WCAG 2.2": "SC 1.4.3 (AA)", "EN 301 549": "9.1.4.3"},
        "why_fail": "These runs were measured directly from the rendered DOM with the WCAG "
        "luminance formula and fall below threshold — corroborating (not just "
        "echoing) axe. Treat as a confirmed contrast defect.",
        "why_pass": "The independent luminance computation found no sub-threshold text — a "
        "second method agrees the page passes contrast.",
    },
    # ----------------------------------------------------------- PERFORMANCE --
    "cwv-lcp": {
        "title": "Largest Contentful Paint",
        "standard": "Google Core Web Vitals — LCP (good ≤ 2.5s at p75)",
        "url": "https://web.dev/articles/lcp",
        "frameworks": {"Core Web Vitals": "LCP ≤ 2.5s", "Google Search Essentials": "Page experience"},
        "why_fail": "LCP > 2.5s means the main content feels slow to load; it is a ranking "
        "and bounce factor. Optimise the LCP element (image priority, server "
        "response, render-blocking).",
        "why_pass": "LCP ≤ 2.5s — primary content paints quickly for most real users.",
    },
    "cwv-cls": {
        "title": "Cumulative Layout Shift",
        "standard": "Google Core Web Vitals — CLS (good ≤ 0.1)",
        "url": "https://web.dev/articles/cls",
        "frameworks": {"Core Web Vitals": "CLS ≤ 0.1", "Google Search Essentials": "Page experience"},
        "why_fail": "CLS > 0.1 means content jumps as it loads, causing mis-clicks. Reserve "
        "space: set width/height on media and avoid late-injected content.",
        "why_pass": "CLS ≤ 0.1 — the layout is visually stable as it loads.",
    },
    "cwv-inp": {
        "title": "Interaction to Next Paint",
        "standard": "Google Core Web Vitals — INP (good ≤ 200ms; replaced FID Mar 2024)",
        "url": "https://web.dev/articles/inp",
        "frameworks": {"Core Web Vitals": "INP ≤ 200ms", "Google Search Essentials": "Page experience"},
        "why_fail": "INP > 200ms (or high Total Blocking Time) makes the page feel "
        "unresponsive to taps/clicks. Break up long JS tasks and defer "
        "non-critical work.",
        "why_pass": "INP ≤ 200ms — interactions feel responsive.",
    },
    "unsized-images": {
        "title": "Images without explicit dimensions",
        "standard": "web.dev — set explicit width/height to prevent layout shift (CLS)",
        "url": "https://web.dev/articles/optimize-cls",
        "frameworks": {"Core Web Vitals": "CLS"},
        "why_fail": "Images without width/height attributes reserve no space, so the page "
        "reflows when they load (CLS). Set intrinsic width and height (or "
        "aspect-ratio).",
        "why_pass": "Images declare dimensions, so space is reserved and layout doesn't shift.",
    },
    "uses-responsive-images": {
        "title": "Appropriately-sized / responsive images",
        "standard": "web.dev — serve responsive images (srcset/sizes)",
        "url": "https://web.dev/articles/serve-responsive-images",
        "frameworks": {"Core Web Vitals": "LCP / payload"},
        "why_fail": "Serving oversized images wastes bytes and delays LCP on mobile. Use "
        "srcset/sizes to ship device-appropriate dimensions.",
        "why_pass": "Images are sized to the device, minimising wasted bytes and speeding LCP.",
    },
    "uses-optimized-images": {
        "title": "Efficiently-encoded images",
        "standard": "web.dev — efficiently encode images",
        "url": "https://web.dev/articles/uses-optimized-images",
        "frameworks": {"Core Web Vitals": "LCP / payload"},
        "why_fail": "Under-compressed images inflate payload and slow loads. Compress to an appropriate quality.",
        "why_pass": "Images are compressed efficiently, reducing transfer size.",
    },
    "modern-image-formats": {
        "title": "Next-gen image formats (WebP/AVIF)",
        "standard": "web.dev — use WebP/AVIF",
        "url": "https://web.dev/articles/uses-webp-images",
        "frameworks": {"Core Web Vitals": "LCP / payload"},
        "why_fail": "JPEG/PNG are larger than WebP/AVIF at equal quality. Serve modern formats with fallbacks.",
        "why_pass": "Images use modern formats, cutting bytes at equal quality.",
    },
    "render-blocking-resources": {
        "title": "Render-blocking resources",
        "standard": "web.dev — eliminate render-blocking resources",
        "url": "https://web.dev/articles/render-blocking-resources",
        "frameworks": {"Core Web Vitals": "LCP / FCP"},
        "why_fail": "Synchronous CSS/JS in the head delays first paint. Inline critical "
        "CSS, defer/async non-critical JS.",
        "why_pass": "No resources block the initial render, so first paint is fast.",
    },
    "uses-text-compression": {
        "title": "Text compression (gzip/br)",
        "standard": "web.dev — enable text compression",
        "url": "https://web.dev/articles/uses-text-compression",
        "frameworks": {"Core Web Vitals": "payload"},
        "why_fail": "Uncompressed text assets transfer far more bytes. Enable gzip or Brotli at the server/CDN.",
        "why_pass": "Text assets are compressed (gzip/Brotli), reducing transfer size.",
    },
    "uses-long-cache-ttl": {
        "title": "Efficient cache policy",
        "standard": "web.dev — serve static assets with an efficient cache policy",
        "url": "https://web.dev/articles/uses-long-cache-ttl",
        "frameworks": {"Core Web Vitals": "repeat-view"},
        "why_fail": "Short/absent cache TTLs force re-downloads on repeat visits. Set long "
        "max-age with content hashing for static assets.",
        "why_pass": "Static assets cache long-term, speeding repeat visits.",
    },
    "font-display": {
        "title": "Font display strategy",
        "standard": "web.dev / CSS font-display — avoid invisible text during webfont load",
        "url": "https://web.dev/articles/font-display",
        "frameworks": {"Core Web Vitals": "FCP / CLS"},
        "why_fail": "Default font loading can hide text (FOIT) until the webfont arrives. "
        "Use font-display: swap (or optional).",
        "why_pass": "font-display avoids invisible text while webfonts load.",
    },
    "third-party-summary": {
        "title": "Third-party script impact",
        "standard": "web.dev — reduce the impact of third-party code",
        "url": "https://web.dev/articles/third-party-summary",
        "frameworks": {"Core Web Vitals": "INP / TBT", "OWASP": "Third-party / supply chain"},
        "why_fail": "Heavy third-party scripts block the main thread (hurting INP) and add "
        "a supply-chain surface. Audit, defer or remove non-essential tags.",
        "why_pass": "Third-party code is limited/deferred, sparing the main thread.",
    },
    # ----------------------------------------------------- SECURITY HEADERS --
    "hsts": {
        "title": "HTTP Strict Transport Security",
        "standard": "RFC 6797 (HSTS) + OWASP Secure Headers Project",
        "url": "https://owasp.org/www-project-secure-headers/#http-strict-transport-security",
        "frameworks": {
            "IETF": "RFC 6797",
            "OWASP": "Secure Headers",
            "NIST CSF 2.0": "PR.DS-02 (data-in-transit)",
            "ISO 27001:2022": "A.8.24 Use of cryptography",
            "CIS Controls v8": "3.10 Encrypt data in transit",
            "ACSC Essential Eight": "out of direct scope (E8 is endpoint-focused; see ACSC TLS hardening guidance)",
        },
        "why_fail": "Without HSTS, a first/parallel http request can be MITM-downgraded. "
        "Send Strict-Transport-Security: max-age=31536000; includeSubDomains "
        "(consider preload).",
        "why_pass": "HSTS forces https for the max-age window, preventing protocol "
        "downgrade and cookie-stripping attacks.",
    },
    "csp": {
        "title": "Content Security Policy",
        "standard": "W3C CSP Level 3 + OWASP Secure Headers",
        "url": "https://owasp.org/www-project-secure-headers/#content-security-policy",
        "frameworks": {
            "W3C": "CSP Level 3",
            "OWASP": "Secure Headers / A03 Injection",
            "NIST CSF 2.0": "PR.PS-05 / PR.DS",
            "ISO 27001:2022": "A.8.26 Application security requirements",
            "CIS Controls v8": "16.11 Leverage secure config for apps",
        },
        "why_fail": "No CSP leaves XSS with no defence-in-depth. Define a policy "
        "restricting script/style/connect sources (start report-only, then "
        "enforce).",
        "why_pass": "A CSP constrains which sources can execute/load, mitigating XSS and "
        "data-exfil even if a markup injection slips through.",
    },
    "x-content-type-options": {
        "title": "X-Content-Type-Options: nosniff",
        "standard": "WHATWG Fetch (MIME sniffing) + OWASP Secure Headers",
        "url": "https://owasp.org/www-project-secure-headers/#x-content-type-options",
        "frameworks": {
            "WHATWG": "Fetch",
            "OWASP": "Secure Headers",
            "NIST CSF 2.0": "PR.PS-05",
            "ISO 27001:2022": "A.8.26",
            "CIS Controls v8": "16.11",
        },
        "why_fail": "Without nosniff, browsers may MIME-sniff responses and execute "
        "uploaded content as script. Send X-Content-Type-Options: nosniff.",
        "why_pass": "nosniff stops MIME-type sniffing, so responses are treated only as their declared type.",
    },
    "x-frame-options": {
        "title": "Clickjacking protection (X-Frame-Options / frame-ancestors)",
        "standard": "RFC 7034 (X-Frame-Options) / CSP frame-ancestors + OWASP",
        "url": "https://owasp.org/www-project-secure-headers/#x-frame-options",
        "frameworks": {
            "IETF": "RFC 7034",
            "OWASP": "Secure Headers / Clickjacking",
            "NIST CSF 2.0": "PR.PS-05",
            "ISO 27001:2022": "A.8.26",
            "CIS Controls v8": "16.11",
        },
        "why_fail": "Without framing protection the site can be embedded in an attacker "
        "iframe for clickjacking. Send X-Frame-Options: DENY or CSP "
        "frame-ancestors 'self'.",
        "why_pass": "Framing is restricted, preventing the page being overlaid for clickjacking.",
    },
    "referrer-policy": {
        "title": "Referrer-Policy",
        "standard": "W3C Referrer Policy + OWASP Secure Headers",
        "url": "https://owasp.org/www-project-secure-headers/#referrer-policy",
        "frameworks": {
            "W3C": "Referrer Policy",
            "OWASP": "Secure Headers",
            "NIST CSF 2.0": "PR.DS",
            "ISO 27001:2022": "A.5.34 Privacy/PII",
        },
        "why_fail": "A permissive/absent policy leaks full URLs (paths, tokens) to "
        "third parties via Referer. Set e.g. strict-origin-when-cross-origin.",
        "why_pass": "Referrer-Policy limits what URL data is sent cross-origin, reducing "
        "leakage of sensitive paths/params.",
    },
    "permissions-policy": {
        "title": "Permissions-Policy",
        "standard": "W3C Permissions Policy + OWASP Secure Headers",
        "url": "https://owasp.org/www-project-secure-headers/#permissions-policy",
        "frameworks": {
            "W3C": "Permissions Policy",
            "OWASP": "Secure Headers",
            "NIST CSF 2.0": "PR.PS-05",
            "ISO 27001:2022": "A.8.26",
        },
        "why_fail": "Without a Permissions-Policy, embedded/third-party code may access "
        "camera, mic, geolocation, etc. Disable features you don't use.",
        "why_pass": "Powerful browser features are explicitly restricted to what the site needs.",
    },
    # -------------------------------------------------------------- TLS ------
    "tls-version": {
        "title": "TLS protocol versions",
        "standard": "Mozilla Server Side TLS + NIST SP 800-52 Rev.2",
        "url": "https://wiki.mozilla.org/Security/Server_Side_TLS",
        "frameworks": {
            "Mozilla": "Server Side TLS",
            "NIST": "SP 800-52r2",
            "PCI DSS 4.0": "4.2.1",
            "NIST CSF 2.0": "PR.DS-02",
            "ISO 27001:2022": "A.8.24",
            "CIS Controls v8": "3.10",
        },
        "why_fail": "SSLv3/TLS1.0/1.1 are deprecated and exploitable (POODLE/BEAST). "
        "Disable them; require TLS1.2+ and prefer TLS1.3.",
        "why_pass": "Only TLS1.2/1.3 offered — no deprecated protocols, matching Mozilla Intermediate/Modern guidance.",
    },
    "tls-cipher": {
        "title": "Cipher suites / key strength",
        "standard": "Mozilla Server Side TLS (cipher ordering)",
        "url": "https://ssl-config.mozilla.org/",
        "frameworks": {
            "Mozilla": "Server Side TLS",
            "NIST": "SP 800-52r2",
            "PCI DSS 4.0": "4.2.1",
            "NIST CSF 2.0": "PR.DS-02",
            "ISO 27001:2022": "A.8.24",
        },
        "why_fail": "Weak ciphers (RC4, 3DES, export, non-PFS) allow decryption/MITM. "
        "Offer only strong AEAD suites with forward secrecy.",
        "why_pass": "Only strong, forward-secret AEAD ciphers offered.",
    },
    "cert-validity": {
        "title": "Certificate validity & chain",
        "standard": "CA/Browser Forum Baseline Requirements + RFC 5280",
        "url": "https://cabforum.org/baseline-requirements-documents/",
        "frameworks": {
            "CA/Browser Forum": "Baseline Requirements",
            "IETF": "RFC 5280",
            "NIST CSF 2.0": "PR.DS-02",
            "ISO 27001:2022": "A.8.24",
        },
        "why_fail": "Expired, self-signed, name-mismatched or incomplete-chain certs throw "
        "browser warnings and break trust. Use a publicly-trusted cert with a "
        "complete chain and automated renewal.",
        "why_pass": "A trusted, in-date certificate with a valid chain and matching name.",
    },
    # ----------------------------------------------------------- DNS ---------
    "caa": {
        "title": "CAA record",
        "standard": "RFC 8659 (DNS Certification Authority Authorization)",
        "url": "https://datatracker.ietf.org/doc/html/rfc8659",
        "frameworks": {
            "IETF": "RFC 8659",
            "NIST CSF 2.0": "PR.DS-02",
            "ISO 27001:2022": "A.8.24",
            "CIS Controls v8": "3.10",
        },
        "why_fail": "No CAA record means any public CA may issue a certificate for the "
        "domain, widening mis-issuance risk. Publish CAA pinning your CA(s) "
        "plus an iodef contact.",
        "why_pass": "CAA restricts certificate issuance to nominated CAs, limiting mis-issuance.",
    },
    "dnssec": {
        "title": "DNSSEC",
        "standard": "RFC 4033-4035 + NIST SP 800-81",
        "url": "https://datatracker.ietf.org/doc/html/rfc4033",
        "frameworks": {
            "IETF": "RFC 4033-4035",
            "NIST": "SP 800-81",
            "NIST CSF 2.0": "PR.DS / PR.AA",
            "ISO 27001:2022": "A.8.20 Network security",
        },
        "why_fail": "Without DNSSEC, responses can be spoofed/cache-poisoned. Enable "
        "DNSSEC signing at the registrar/DNS host.",
        "why_pass": "DNSSEC-signed zone (AD flag) lets resolvers verify records weren't tampered with.",
    },
    # ----------------------------------------------------------- EMAIL -------
    "spf": {
        "title": "SPF record",
        "standard": "RFC 7208 (Sender Policy Framework)",
        "url": "https://datatracker.ietf.org/doc/html/rfc7208",
        "frameworks": {
            "IETF": "RFC 7208",
            "NIST CSF 2.0": "PR.DS",
            "CIS Controls v8": "9.5 Implement DMARC",
            "ACSC Essential Eight": "n/a (E8 core); see ACSC 'How to combat fake emails' (SPF/DKIM/DMARC)",
        },
        "why_fail": "Missing/over-broad SPF lets others spoof your domain in email. Publish "
        "an SPF record authorising only legitimate senders, ending in ~all/-all.",
        "why_pass": "SPF authorises exactly your sending sources, so spoofed mail fails origin checks.",
    },
    "dmarc": {
        "title": "DMARC record",
        "standard": "RFC 7489 (DMARC)",
        "url": "https://datatracker.ietf.org/doc/html/rfc7489",
        "frameworks": {
            "IETF": "RFC 7489",
            "NIST CSF 2.0": "PR.DS / DE.CM",
            "CIS Controls v8": "9.5 Implement DMARC",
            "ACSC Essential Eight": "n/a (E8 core); ACSC strongly recommends DMARC reject for .gov.au",
        },
        "why_fail": "No DMARC (or p=none / no rua) means spoofing isn't enforced or even "
        "monitored. Publish DMARC with rua reporting and move toward "
        "p=quarantine then p=reject once reports are clean.",
        "why_pass": "DMARC ties SPF/DKIM to the From domain, enforces a policy on failures "
        "and (with rua) gives visibility into abuse.",
    },
    "dkim": {
        "title": "DKIM signing",
        "standard": "RFC 6376 (DomainKeys Identified Mail)",
        "url": "https://datatracker.ietf.org/doc/html/rfc6376",
        "frameworks": {
            "IETF": "RFC 6376",
            "NIST CSF 2.0": "PR.DS",
            "CIS Controls v8": "9.5",
            "ACSC Essential Eight": "n/a (E8 core); part of ACSC email hardening",
        },
        "why_fail": "Without DKIM, receivers can't cryptographically verify the message "
        "wasn't altered or forged. Enable DKIM signing and publish the public "
        "key in DNS.",
        "why_pass": "DKIM cryptographically signs mail, letting receivers confirm integrity "
        "and authenticity — and satisfying DMARC alignment.",
    },
    # ----------------------------------------------- DISCOVERY / AI POLICY ---
    "robots-txt": {
        "title": "robots.txt",
        "standard": "RFC 9309 (Robots Exclusion Protocol)",
        "url": "https://datatracker.ietf.org/doc/html/rfc9309",
        "frameworks": {"IETF": "RFC 9309", "Google Search Essentials": "robots.txt"},
        "why_fail": "An absent or over-broad robots.txt either gives crawlers no guidance "
        "or accidentally blocks indexable content. Provide a correct robots.txt "
        "and verify it doesn't Disallow pages you want indexed.",
        "why_pass": "robots.txt is present and scoped so search crawlers can reach content meant to be indexed.",
    },
    "sitemap": {
        "title": "XML sitemap",
        "standard": "sitemaps.org protocol + Google sitemap guidelines",
        "url": "https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview",
        "frameworks": {"sitemaps.org": "0.9", "Google Search Essentials": "Sitemaps"},
        "why_fail": "No XML sitemap slows discovery of new/deep pages, especially on large "
        "sites. Publish a sitemap and declare it in robots.txt / Search Console.",
        "why_pass": "An XML sitemap helps crawlers discover and prioritise pages efficiently.",
    },
    "ai-crawler-policy": {
        "title": "AI-crawler policy (GPTBot/ClaudeBot/Google-Extended/…) ",
        "standard": "REP (RFC 9309) applied to AI user-agents + llms.txt proposal",
        "url": "https://llmstxt.org/",
        "frameworks": {"IETF": "RFC 9309", "llms.txt": "proposal"},
        "why_fail": "No expressed AI policy means AI crawlers default to allowed; if you "
        "intend to allow/deny training or answer-engine use, state it explicitly "
        "in robots.txt (per-bot) and consider an llms.txt.",
        "why_pass": "The site explicitly expresses its stance to AI crawlers, so "
        "training/answer-engine access matches the owner's intent.",
    },
    "security-txt": {
        "title": "security.txt",
        "standard": "RFC 9116 (.well-known/security.txt)",
        "url": "https://datatracker.ietf.org/doc/html/rfc9116",
        "frameworks": {
            "IETF": "RFC 9116",
            "NIST CSF 2.0": "RS.CO (coordination)",
            "ISO 27001:2022": "A.5.5 Contact with authorities / A.6.8 reporting",
        },
        "why_fail": "No security.txt means researchers have no clear, standard channel to "
        "report vulnerabilities. Publish /.well-known/security.txt with a "
        "Contact and Policy.",
        "why_pass": "A security.txt gives finders a defined disclosure contact, speeding responsible reporting.",
    },
}

# axe-core rule id -> KB key (rules not listed fall back to a generic WCAG citation).
AXE_WCAG = {
    "color-contrast": "color-contrast",
    "color-contrast-enhanced": "color-contrast",
    "image-alt": "image-alt",
    "input-image-alt": "image-alt",
    "link-name": "link-name",
    "button-name": "button-name",
    "label": "label",
    "select-name": "label",
    "html-has-lang": "html-has-lang",
    "html-lang-valid": "html-has-lang",
    "document-title": "document-title",
    "region": "landmark-regions",
    "landmark-one-main": "landmark-regions",
    "landmark-unique": "landmark-regions",
    "landmark-complementary-is-top-level": "landmark-regions",
    "bypass": "bypass",
    "skip-link": "bypass",
}


def cite(code):
    """Return the KB entry for a check id (resolving axe rule ids), or a generic
    WCAG/axe fallback so an unmapped axe rule still teaches rather than going bare."""
    if code in STANDARDS:
        return STANDARDS[code]
    if code in AXE_WCAG:
        return STANDARDS[AXE_WCAG[code]]
    return {
        "title": code,
        "standard": "WCAG 2.x (via axe-core rule)",
        "url": f"https://dequeuniversity.com/rules/axe/4.10/{code}",
        "frameworks": {"WCAG 2.2": "see Deque rule reference", "EN 301 549": "Chapter 9"},
        "why_fail": f"axe-core flagged '{code}'. See the linked Deque rule for the exact "
        "WCAG success criterion and remediation.",
        "why_pass": "Not flagged by axe-core for this rule.",
    }


def applied(codes):
    """Ordered, de-duplicated citations for a list of fired check ids — what each
    check script attaches to its JSON under the 'standards' key."""
    seen, out = set(), {}
    for c in codes:
        if c in seen:
            continue
        seen.add(c)
        e = cite(c)
        out[c] = {
            "title": e["title"],
            "standard": e["standard"],
            "url": e["url"],
            "frameworks": e["frameworks"],
            "why_fail": e["why_fail"],
            "why_pass": e["why_pass"],
        }
    return out


def format_text(code, result="fail"):
    """Compact human line for .txt summaries: standard + frameworks + the relevant why."""
    e = cite(code)
    fw = "; ".join(f"{k}: {v}" for k, v in e["frameworks"].items())
    why = e["why_pass"] if result == "pass" else e["why_fail"]
    return (
        f"{e['title']}\n    standard: {e['standard']}\n    frameworks: {fw}\n"
        f"    {'why it passes' if result == 'pass' else 'why it fails'}: {why}\n"
        f"    ref: {e['url']}"
    )


def main(argv):
    if argv and argv[0] == "--list":
        for code, e in STANDARDS.items():
            print(f"{code:28} {e['standard']}")
        return 0
    if argv:
        for code in argv:
            print(format_text(code))
            print()
        return 0
    json.dump(STANDARDS, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
