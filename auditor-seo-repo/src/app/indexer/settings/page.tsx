"use client";

import { useEffect, useState } from "react";
import { Code, Copy, Check, Info, Globe, Shield, RefreshCw } from "lucide-react";
import { useLanguage } from "@/lib/i18n/LanguageProvider";

interface DomainOpt {
  id: string;
  domain: string;
  apiKey: string;
  moneyUrl: string | null;
  allowedBots: string;
}

export default function IndexerSettingsPage() {
  const { t } = useLanguage();
  const [domains, setDomains] = useState<DomainOpt[]>([]);
  const [selectedDomainId, setSelectedDomainId] = useState("");
  const [publicUrl, setPublicUrl] = useState("http://localhost:3001");
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(true);
  const [isLarge, setIsLarge] = useState(false);
  const [integrationType, setIntegrationType] = useState<"php" | "phpStatic" | "nginx">("php");
  // Words from Indexer -> Dictionary. They drive the generated slugs, subdomains and page text,
  // so the doorway crawl space matches the user's niche instead of a generic hardcoded list.
  const [dictionary, setDictionary] = useState<string[]>([]);

  useEffect(() => {
    // Load public URL from localStorage if set
    const savedUrl = localStorage.getItem("indexerPublicUrl");
    if (savedUrl) setPublicUrl(savedUrl);

    setIsLarge(window.innerWidth > 768);

    // Fetch domains
    const fetchDomains = async () => {
      try {
        const res = await fetch("/api/indexer/domains");
        if (res.ok) {
          const d = await res.json();
          setDomains(d);
          if (d.length > 0) setSelectedDomainId(d[0].id);
        }
      } catch (e) {
        console.error(e);
      } finally {
        setLoading(false);
      }
    };
    fetchDomains();

    // Load the user's dictionary to embed into the generated script
    fetch("/api/indexer/dictionary")
      .then(r => (r.ok ? r.json() : []))
      .then(d => { if (Array.isArray(d)) setDictionary(d); })
      .catch(() => {});
  }, []);

  const savePublicUrl = (url: string) => {
    setPublicUrl(url);
    localStorage.setItem("indexerPublicUrl", url);
  };

  const selectedDomain = domains.find(d => d.id === selectedDomainId);

  // Effective ALLOWED_BOTS emitted into the doorway script. Tokens: google,bing,yandex,mailru,ai,ai-training.
  // The script enforces this list. Legacy domains (saved before AI existed, so no ai/ai-training token)
  // are upgraded on the fly to keep serving search + AI answer bots, matching current behaviour.
  const effectiveAllowedBots = (() => {
    const raw = (selectedDomain?.allowedBots || "google,bing,yandex,mailru,ai").toLowerCase();
    let tokens = raw.split(",").map(s => s.trim()).filter(Boolean);
    // "cfg" marker = record saved with the new checkboxes (explicit config, respect exactly).
    // No marker = legacy record from before AI existed -> upgrade to keep serving search + AI answer.
    const explicit = tokens.includes("cfg");
    tokens = tokens.filter(t => t !== "cfg");
    if (!explicit) { const set = new Set(tokens); set.add("mailru"); set.add("ai"); tokens = Array.from(set); }
    return tokens.join(",");
  })();

  // ── Word pool embedded into the doorway script ──
  // Taken from Indexer -> Dictionary so the generated slugs/subdomains/text match the user's
  // niche. Words are slugified (lowercase, a-z0-9 and hyphens) because they end up inside URLs.
  // Falls back to a generic pool when the dictionary is still empty.
  const FALLBACK_WORDS = [
    "deals", "shop", "discount", "sale", "online", "price", "review", "best", "cheap", "quality", "free", "shipping",
    "guide", "compare", "catalog", "offers", "store", "market", "budget", "premium", "rating", "top", "choice", "trends",
    "models", "brands", "series", "edition", "bundle", "coupon", "outlet", "express", "global", "local", "prime", "value",
    "expert", "buyers", "picks", "list", "index", "archive", "digest", "report", "insight", "update", "season", "collection",
  ];

  const wordPool = (() => {
    const cleaned = dictionary
      .map(w => String(w).toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, ""))
      .filter(w => w.length >= 2 && w.length <= 30);
    const unique = Array.from(new Set(cleaned));
    // A tiny dictionary would make URLs repeat, so top it up with the fallback pool.
    return unique.length >= 20 ? unique : Array.from(new Set([...unique, ...FALLBACK_WORDS]));
  })();

  const usingDictionary = dictionary.length > 0;

  // Render as a PHP array literal, 12 words per line for readability
  const phpWordArray = (() => {
    const lines: string[] = [];
    for (let i = 0; i < wordPool.length; i += 12) {
      lines.push("        " + wordPool.slice(i, i + 12).map(w => `"${w}"`).join(", "));
    }
    return lines.join(",\n");
  })();

  // Generate PHP Script Content Dynamically (Standard Redirect)
  const phpScriptContent = `<?php
// ─── OpenGSC Private Indexer Doorway Script ───
// Save as index.php in your doorway root folder.
// Ensure you have wildcard DNS and rewrite rules to route all traffic to index.php.

define('API_URL', '${publicUrl.replace(/\/$/, "")}/api/indexer/webhook');
define('API_KEY', '${selectedDomain?.apiKey || "YOUR_DOMAIN_API_KEY_HERE"}');
define('REDIRECT_TARGET', '${selectedDomain?.moneyUrl || "https://your-money-site.com"}');
// Root domain used to spawn endless subdomains. Requires wildcard DNS (*.domain -> this server).
define('BASE_DOMAIN', '${selectedDomain?.domain || "your-doorway-domain.com"}');
// Scheme for generated subdomain links. Keep 'http' unless you have a WILDCARD SSL cert:
// a plain Let's Encrypt cert covers only the root domain, so https://sub.domain would fail
// the TLS handshake and crawlers would drop every subdomain URL without fetching it.
// Switch to 'https' after installing a *.domain certificate.
define('SUBDOMAIN_SCHEME', 'http');
// Crawl-space size: how many internal + subdomain links each served page emits.
// This is the "bait": every page spawns dozens of new URLs, so crawlers never run out.
define('LINKS_PER_PAGE_MIN', 18);
define('LINKS_PER_PAGE_MAX', 34);
define('SUBDOMAIN_LINKS_MIN', 4);
define('SUBDOMAIN_LINKS_MAX', 9);
// Tokens enforced below: google,bing,yandex,mailru,ai (AI answer/GEO), ai-training.
// Uncheck a crawler in the panel -> its token drops here -> that bot stops being served.
define('ALLOWED_BOTS', '${effectiveAllowedBots}');
define('STRICT_VERIFICATION', true); // Verify search bots via Reverse & Forward DNS lookup

// ─── CRAWL-SPACE HELPERS ───
// Builds an effectively unlimited URL slug: 2-4 dictionary words + a number.
// 60 words -> millions of combinations per length, so the crawl space never repeats.
function build_slug($words) {
    $n = rand(2, 4);
    $parts = array();
    for ($i = 0; $i < $n; $i++) { $parts[] = $words[array_rand($words)]; }
    return implode('-', $parts) . '-' . rand(1, 9999999);
}

// Turns a slug back into a readable anchor ("winter-deals-42" -> "Winter deals")
function slug_to_anchor($slug) {
    $clean = preg_replace('#-\\d+$#', '', $slug);
    return ucfirst(str_replace('-', ' ', $clean));
}

// ─── BOT DETECTION LOGIC ───
function get_client_ip() {
    if (!empty($_SERVER['HTTP_CF_CONNECTING_IP'])) return trim($_SERVER['HTTP_CF_CONNECTING_IP']);
    if (!empty($_SERVER['HTTP_X_FORWARDED_FOR'])) {
        $ips = explode(',', $_SERVER['HTTP_X_FORWARDED_FOR']);
        return trim($ips[0]);
    }
    return isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : '0.0.0.0';
}

$user_agent = isset($_SERVER['HTTP_USER_AGENT']) ? $_SERVER['HTTP_USER_AGENT'] : '';
$ip = get_client_ip();
$uri = isset($_SERVER['REQUEST_URI']) ? $_SERVER['REQUEST_URI'] : '/';
$host = isset($_SERVER['HTTP_HOST']) ? $_SERVER['HTTP_HOST'] : '';
$referer = isset($_SERVER['HTTP_REFERER']) ? $_SERVER['HTTP_REFERER'] : '';

$is_bot = false;
$detected_bot_type = '';
$ua_lower = strtolower($user_agent);

// AI / GEO crawlers. Split into "answer" (live citation -> traffic) and "training" (model ingest).
$ai_answer_bots   = array('oai-searchbot', 'chatgpt-user', 'perplexitybot', 'perplexity-user', 'claudebot', 'claude-user', 'duckassistbot', 'google-extended');
$ai_training_bots = array('gptbot', 'ccbot', 'anthropic-ai', 'bytespider', 'meta-externalagent', 'meta-externalfetcher', 'applebot-extended', 'cohere-ai', 'cohere-training', 'amazonbot', 'diffbot', 'imagesift', 'omgili', 'timpibot', 'youbot');
function ua_matches_any($ua, $list) {
    foreach ($list as $needle) { if (strpos($ua, $needle) !== false) return true; }
    return false;
}

if (strpos($ua_lower, 'googlebot') !== false || strpos($ua_lower, 'google-inspectiontool') !== false || strpos($ua_lower, 'googleother') !== false || strpos($ua_lower, 'google-co') !== false || strpos($ua_lower, 'storebot-google') !== false || strpos($ua_lower, 'google-site-verification') !== false) {
    $is_bot = true;
    $detected_bot_type = 'google';
} elseif (strpos($ua_lower, 'bingbot') !== false || strpos($ua_lower, 'bingpreview') !== false || strpos($ua_lower, 'msnbot') !== false) {
    $is_bot = true;
    $detected_bot_type = 'bing';
} elseif (strpos($ua_lower, 'yandex') !== false) {
    $is_bot = true;
    $detected_bot_type = 'yandex';
} elseif (strpos($ua_lower, 'mail.ru') !== false || strpos($ua_lower, 'mailru') !== false) {
    $is_bot = true;
    $detected_bot_type = 'mailru';
} elseif (ua_matches_any($ua_lower, $ai_answer_bots)) {
    // AI answer/search crawler (GEO traffic source)
    $is_bot = true;
    $detected_bot_type = 'ai';
    $ai_kind = 'answer';
} elseif (ua_matches_any($ua_lower, $ai_training_bots)) {
    // AI training crawler
    $is_bot = true;
    $detected_bot_type = 'ai';
    $ai_kind = 'training';
} elseif (strpos($ua_lower, 'bot') !== false || strpos($ua_lower, 'crawler') !== false || strpos($ua_lower, 'spider') !== false) {
    $is_bot = true;
    $detected_bot_type = 'other';
}

// ─── ENFORCE ALLOWED_BOTS (panel checkboxes) ───
$allowed = array_map('trim', explode(',', strtolower(ALLOWED_BOTS)));

// Search engines: served only if their token is enabled, else treated as a normal visitor
if (in_array($detected_bot_type, array('google', 'bing', 'yandex', 'mailru')) && !in_array($detected_bot_type, $allowed)) {
    $is_bot = false;
    $detected_bot_type = '';
}
// AI answer bots: served only if 'ai' is enabled
if (!empty($ai_kind) && $ai_kind === 'answer' && !in_array('ai', $allowed)) {
    $is_bot = false;
}
// AI training bots: served only if 'ai-training' is enabled, else 403 (no doorway, no money redirect)
if (!empty($ai_kind) && $ai_kind === 'training' && !in_array('ai-training', $allowed)) {
    send_log_ping(false, 403);
    header("HTTP/1.1 403 Forbidden");
    exit;
}

// Double DNS lookup (rDNS + Forward IP match) to verify real search engines
if ($is_bot && STRICT_VERIFICATION && in_array($detected_bot_type, array('google', 'yandex', 'bing', 'mailru'))) {
    $is_bot = verify_bot_dns($ip, $detected_bot_type);
}

// ─── ROUTE VISITOR ───
if ($is_bot) {
    // NOTE: no 304 handling here on purpose. A 304 has an empty body, so the crawler would
    // never see the queued money-site links — which is the whole point of serving the bot.
    // Always return a fresh 200 and tell caches not to reuse it.

    // Send a 200 log ping — the response carries queued money-site URLs to inject below
    $queue_links = send_log_ping(false, 200, $detected_bot_type);
    header("Cache-Control: no-cache, no-store, must-revalidate");
    header("Pragma: no-cache");
    header("Content-Type: text/html; charset=UTF-8");

    // 2. Render dynamic messy doorway content
    // Seed the generator from the requested URL: the same URL always renders the same page,
    // so re-crawls see stable content and a stable link graph (looks like a real site),
    // while every NEW url spawns its own fresh branch of the crawl space.
    mt_srand(crc32($host . $uri));

    // Word pool from your OpenGSC dictionary (Indexer -> Dictionary). Drives slugs, subdomains and text.
    $niche_words = array(
${phpWordArray}
    );
    $rand_title = ucfirst($niche_words[array_rand($niche_words)]) . " " . $niche_words[array_rand($niche_words)] . " " . $niche_words[array_rand($niche_words)];

    echo "<!DOCTYPE html><html><head><title>" . htmlspecialchars($rand_title) . "</title></head><body style='font-family: sans-serif; padding: 20px;'>";
    echo "<h1>" . htmlspecialchars($rand_title) . "</h1>";
    echo "<p>Crawl pool semantic markup sandbox:</p>";

    // Generate text mash
    echo "<div>";
    for ($i = 0; $i < 60; $i++) {
        echo htmlspecialchars($niche_words[array_rand($niche_words)]) . " ";
    }
    echo "</div>";

    // ─── Money-site links: ONLY from the OpenGSC crawl queue ───
    // Note: REDIRECT_TARGET is the human decoy (a big neutral site) and is deliberately
    // NOT linked here — linking it would pass equity to someone else's domain and create
    // a doorway->decoy footprint. Real money-site URLs come from Indexer -> Queue.
    $anchors = array("official site", "read more", "best offer", "visit resource", "full guide", "recommended", "learn more", "see details");
    if (!empty($queue_links)) {
        echo "<p>Recommended resources:</p><ul>";
        foreach ($queue_links as $q_url) {
            if (!is_string($q_url) || $q_url === '') continue;
            $slug = trim(preg_replace('#[^a-z0-9]+#i', ' ', parse_url($q_url, PHP_URL_PATH)));
            $q_anchor = $slug !== '' ? ucfirst($slug) : ucfirst($anchors[array_rand($anchors)]) . " " . $niche_words[array_rand($niche_words)];
            echo "<li><a href='" . htmlspecialchars($q_url) . "'>" . htmlspecialchars($q_anchor) . "</a></li>";
        }
        echo "</ul>";
    }

    // ─── INFINITE CRAWL SPACE (the bait) ───
    // Every served page emits dozens of brand-new URLs, and each of those does the same.
    // The link graph therefore branches exponentially instead of running as a single chain,
    // so a crawler always has somewhere new to go and keeps coming back for more.
    echo "<h2>" . htmlspecialchars(ucfirst($niche_words[array_rand($niche_words)])) . " sections</h2><ul>";
    $link_count = rand(LINKS_PER_PAGE_MIN, LINKS_PER_PAGE_MAX);
    for ($i = 0; $i < $link_count; $i++) {
        $slug = build_slug($niche_words);
        echo "<li><a href='/?p=" . urlencode($slug) . "'>" . htmlspecialchars(slug_to_anchor($slug)) . "</a></li>";
    }
    echo "</ul>";

    // Endless subdomains — needs wildcard DNS (*.BASE_DOMAIN -> this server).
    // Each subdomain is a fresh host with its own unlimited page space.
    if (BASE_DOMAIN !== '' && strpos(BASE_DOMAIN, 'your-doorway-domain') === false) {
        echo "<h2>More " . htmlspecialchars($niche_words[array_rand($niche_words)]) . "</h2><ul>";
        $sub_count = rand(SUBDOMAIN_LINKS_MIN, SUBDOMAIN_LINKS_MAX);
        for ($i = 0; $i < $sub_count; $i++) {
            $sub = $niche_words[array_rand($niche_words)] . "-" . $niche_words[array_rand($niche_words)] . rand(1, 99999);
            $slug = build_slug($niche_words);
            $sub_url = SUBDOMAIN_SCHEME . "://" . $sub . "." . BASE_DOMAIN . "/?p=" . urlencode($slug);
            echo "<li><a href='" . htmlspecialchars($sub_url) . "'>" . htmlspecialchars(slug_to_anchor($slug)) . "</a></li>";
        }
        echo "</ul>";
    }

    echo "</body></html>";
    exit;
} else {
    // Human visitor or fake bot - trigger redirect webhook and redirect
    send_log_ping(true, 302);
    header("Location: " . REDIRECT_TARGET, true, 302);
    exit;
}

function verify_bot_dns($ip, $bot_type) {
    // Step 1: Reverse DNS lookup
    $hostname = gethostbyaddr($ip);
    if (!$hostname || $hostname === $ip) {
        return false;
    }
    
    // Step 2: Check domain patterns
    $is_valid_domain = false;
    if ($bot_type === 'google') {
        if (preg_match('/\\.googlebot\\.com$/i', $hostname) || preg_match('/\\.google\\.com$/i', $hostname) || preg_match('/\\.googleusercontent\\.com$/i', $hostname)) {
            $is_valid_domain = true;
        }
    } elseif ($bot_type === 'yandex') {
        if (preg_match('/\\.yandex\\.(ru|net|com)$/i', $hostname)) {
            $is_valid_domain = true;
        }
    } elseif ($bot_type === 'bing') {
        if (preg_match('/\\.search\\.msn\\.com$/i', $hostname)) {
            $is_valid_domain = true;
        }
    } elseif ($bot_type === 'mailru') {
        if (preg_match('/\\.mail\\.ru$/i', $hostname)) {
            $is_valid_domain = true;
        }
    }
    
    if (!$is_valid_domain) {
        return false;
    }
    
    // Step 3: Forward DNS lookup to verify original IP
    $resolved_ip = gethostbyname($hostname);
    return ($resolved_ip === $ip);
}

// Sends the crawl log to OpenGSC and returns the money-site URLs from the crawl queue
// that should be injected into this page (so queued pages get discovered by crawlers).
function send_log_ping($is_redirect, $status_code = 200, $bot_type = '') {
    global $user_agent, $ip, $uri, $host, $referer;

    $payload = json_encode(array(
        'apiKey' => API_KEY,
        'url' => 'https://' . $host . $uri,
        'ip' => $ip,
        'userAgent' => $user_agent,
        'statusCode' => $status_code,
        'referer' => $referer,
        'isRedirect' => $is_redirect,
        'botType' => $bot_type
    ));

    $ch = curl_init(API_URL);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, $payload);
    curl_setopt($ch, CURLOPT_HTTPHEADER, array('Content-Type: application/json'));
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    curl_setopt($ch, CURLOPT_SSL_VERIFYHOST, false);
    curl_setopt($ch, CURLOPT_FOLLOWLOCATION, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 5);
    $response = curl_exec($ch);
    curl_close($ch);

    $links = array();
    if ($response) {
        $decoded = json_decode($response, true);
        if (is_array($decoded) && !empty($decoded['links']) && is_array($decoded['links'])) {
            $links = $decoded['links'];
        }
    }
    return $links;
}
`;

  // PHP Static Site Wrapper Content (Cloaks static HTML files)
  const phpStaticWrapperContent = `<?php
// ─── OpenGSC Private Indexer Doorway Script (Static Site Wrapper) ───
// Rename your original static index.html to index_real.html.
// Upload this script as index.php in your root folder.
// This script serves index_real.html to humans, and doorway to bots.

define('API_URL', '${publicUrl.replace(/\/$/, "")}/api/indexer/webhook');
define('API_KEY', '${selectedDomain?.apiKey || "YOUR_DOMAIN_API_KEY_HERE"}');
define('REDIRECT_TARGET', '${selectedDomain?.moneyUrl || "https://your-money-site.com"}'); // human decoy target
define('BASE_DOMAIN', '${selectedDomain?.domain || "your-doorway-domain.com"}'); // needs wildcard DNS (*.domain)
// 'http' unless you installed a WILDCARD SSL cert — https on subdomains without one fails TLS.
define('SUBDOMAIN_SCHEME', 'http');
define('LINKS_PER_PAGE_MIN', 18);
define('LINKS_PER_PAGE_MAX', 34);
define('SUBDOMAIN_LINKS_MIN', 4);
define('SUBDOMAIN_LINKS_MAX', 9);
// Tokens enforced below: google,bing,yandex,mailru,ai (AI answer/GEO), ai-training.
define('ALLOWED_BOTS', '${effectiveAllowedBots}');
define('STRICT_VERIFICATION', true);

// ─── CRAWL-SPACE HELPERS ───
function build_slug($words) {
    $n = rand(2, 4);
    $parts = array();
    for ($i = 0; $i < $n; $i++) { $parts[] = $words[array_rand($words)]; }
    return implode('-', $parts) . '-' . rand(1, 9999999);
}
function slug_to_anchor($slug) {
    $clean = preg_replace('#-\\d+$#', '', $slug);
    return ucfirst(str_replace('-', ' ', $clean));
}

// ─── BOT DETECTION LOGIC ───
$user_agent = isset($_SERVER['HTTP_USER_AGENT']) ? $_SERVER['HTTP_USER_AGENT'] : '';
$ip = isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : '0.0.0.0';
$uri = isset($_SERVER['REQUEST_URI']) ? $_SERVER['REQUEST_URI'] : '/';
$host = isset($_SERVER['HTTP_HOST']) ? $_SERVER['HTTP_HOST'] : '';
$referer = isset($_SERVER['HTTP_REFERER']) ? $_SERVER['HTTP_REFERER'] : '';

$is_bot = false;
$detected_bot_type = '';
$ua_lower = strtolower($user_agent);

// AI / GEO crawlers. Split into "answer" (live citation -> traffic) and "training" (model ingest).
$ai_answer_bots   = array('oai-searchbot', 'chatgpt-user', 'perplexitybot', 'perplexity-user', 'claudebot', 'claude-user', 'duckassistbot', 'google-extended');
$ai_training_bots = array('gptbot', 'ccbot', 'anthropic-ai', 'bytespider', 'meta-externalagent', 'meta-externalfetcher', 'applebot-extended', 'cohere-ai', 'cohere-training', 'amazonbot', 'diffbot', 'imagesift', 'omgili', 'timpibot', 'youbot');
function ua_matches_any($ua, $list) {
    foreach ($list as $needle) { if (strpos($ua, $needle) !== false) return true; }
    return false;
}

if (strpos($ua_lower, 'googlebot') !== false || strpos($ua_lower, 'google-co') !== false) {
    $is_bot = true;
    $detected_bot_type = 'google';
} elseif (strpos($ua_lower, 'bingbot') !== false || strpos($ua_lower, 'bingpreview') !== false) {
    $is_bot = true;
    $detected_bot_type = 'bing';
} elseif (strpos($ua_lower, 'yandex') !== false) {
    $is_bot = true;
    $detected_bot_type = 'yandex';
} elseif (strpos($ua_lower, 'mail.ru') !== false || strpos($ua_lower, 'mailru') !== false) {
    $is_bot = true;
    $detected_bot_type = 'mailru';
} elseif (ua_matches_any($ua_lower, $ai_answer_bots)) {
    $is_bot = true;
    $detected_bot_type = 'ai';
    $ai_kind = 'answer';
} elseif (ua_matches_any($ua_lower, $ai_training_bots)) {
    $is_bot = true;
    $detected_bot_type = 'ai';
    $ai_kind = 'training';
} elseif (strpos($ua_lower, 'bot') !== false || strpos($ua_lower, 'crawler') !== false || strpos($ua_lower, 'spider') !== false) {
    $is_bot = true;
    $detected_bot_type = 'other';
}

// ─── ENFORCE ALLOWED_BOTS (panel checkboxes) ───
$allowed = array_map('trim', explode(',', strtolower(ALLOWED_BOTS)));
if (in_array($detected_bot_type, array('google', 'bing', 'yandex', 'mailru')) && !in_array($detected_bot_type, $allowed)) {
    $is_bot = false;
    $detected_bot_type = '';
}
if (!empty($ai_kind) && $ai_kind === 'answer' && !in_array('ai', $allowed)) {
    $is_bot = false;
}
if (!empty($ai_kind) && $ai_kind === 'training' && !in_array('ai-training', $allowed)) {
    send_log_ping(false, 403);
    header("HTTP/1.1 403 Forbidden");
    exit;
}

if ($is_bot && STRICT_VERIFICATION && in_array($detected_bot_type, array('google', 'yandex', 'bing', 'mailru'))) {
    $is_bot = verify_bot_dns($ip, $detected_bot_type);
}

// ─── ROUTE VISITOR ───
if ($is_bot) {
    // No 304 handling: an empty 304 body would hide the queued money-site links from crawlers.
    $queue_links = send_log_ping(false, 200, $detected_bot_type);
    header("Cache-Control: no-cache, no-store, must-revalidate");
    header("Pragma: no-cache");
    header("Content-Type: text/html; charset=UTF-8");

    // Stable per-URL rendering + endless new branches (see main script for rationale)
    mt_srand(crc32($host . $uri));

    // Word pool from your OpenGSC dictionary (Indexer -> Dictionary). Drives slugs, subdomains and text.
    $niche_words = array(
${phpWordArray}
    );
    $rand_title = ucfirst($niche_words[array_rand($niche_words)]) . " " . $niche_words[array_rand($niche_words)] . " " . $niche_words[array_rand($niche_words)];

    echo "<!DOCTYPE html><html><head><title>" . htmlspecialchars($rand_title) . "</title></head><body style='font-family: sans-serif; padding: 20px;'>";
    echo "<h1>" . htmlspecialchars($rand_title) . "</h1>";
    echo "<p>Crawl pool semantic markup sandbox:</p>";
    
    echo "<div>";
    for ($i = 0; $i < 60; $i++) {
        echo htmlspecialchars($niche_words[array_rand($niche_words)]) . " ";
    }
    echo "</div>";

    // ─── Money-site links: ONLY from the OpenGSC crawl queue ───
    // REDIRECT_TARGET is the human decoy and is deliberately NOT linked here.
    $anchors = array("official site", "read more", "best offer", "visit resource", "full guide", "recommended", "learn more", "see details");
    if (!empty($queue_links)) {
        echo "<p>Recommended resources:</p><ul>";
        foreach ($queue_links as $q_url) {
            if (!is_string($q_url) || $q_url === '') continue;
            $slug = trim(preg_replace('#[^a-z0-9]+#i', ' ', parse_url($q_url, PHP_URL_PATH)));
            $q_anchor = $slug !== '' ? ucfirst($slug) : ucfirst($anchors[array_rand($anchors)]) . " " . $niche_words[array_rand($niche_words)];
            echo "<li><a href='" . htmlspecialchars($q_url) . "'>" . htmlspecialchars($q_anchor) . "</a></li>";
        }
        echo "</ul>";
    }

    // ─── INFINITE CRAWL SPACE (the bait) ───
    echo "<h2>" . htmlspecialchars(ucfirst($niche_words[array_rand($niche_words)])) . " sections</h2><ul>";
    $link_count = rand(LINKS_PER_PAGE_MIN, LINKS_PER_PAGE_MAX);
    for ($i = 0; $i < $link_count; $i++) {
        $slug = build_slug($niche_words);
        echo "<li><a href='/?p=" . urlencode($slug) . "'>" . htmlspecialchars(slug_to_anchor($slug)) . "</a></li>";
    }
    echo "</ul>";

    // Endless subdomains — needs wildcard DNS (*.BASE_DOMAIN -> this server)
    if (BASE_DOMAIN !== '' && strpos(BASE_DOMAIN, 'your-doorway-domain') === false) {
        echo "<h2>More " . htmlspecialchars($niche_words[array_rand($niche_words)]) . "</h2><ul>";
        $sub_count = rand(SUBDOMAIN_LINKS_MIN, SUBDOMAIN_LINKS_MAX);
        for ($i = 0; $i < $sub_count; $i++) {
            $sub = $niche_words[array_rand($niche_words)] . "-" . $niche_words[array_rand($niche_words)] . rand(1, 99999);
            $slug = build_slug($niche_words);
            $sub_url = SUBDOMAIN_SCHEME . "://" . $sub . "." . BASE_DOMAIN . "/?p=" . urlencode($slug);
            echo "<li><a href='" . htmlspecialchars($sub_url) . "'>" . htmlspecialchars(slug_to_anchor($slug)) . "</a></li>";
        }
        echo "</ul>";
    }

    echo "</body></html>";
    exit;
} else {
    // Human visitor: serve static HTML from index_real.html
    send_log_ping(false, 200);
    if (file_exists('index_real.html')) {
        include 'index_real.html';
    } else {
        echo "<!DOCTYPE html><html><head><title>Welcome</title></head><body><h1>Welcome to our static site</h1><p>Please upload index_real.html</p></body></html>";
    }
    exit;
}

function verify_bot_dns($ip, $bot_type) {
    $hostname = gethostbyaddr($ip);
    if (!$hostname || $hostname === $ip) return false;
    
    $is_valid_domain = false;
    if ($bot_type === 'google') {
        if (preg_match('/\\.googlebot\\.com$/i', $hostname) || preg_match('/\\.google\\.com$/i', $hostname)) $is_valid_domain = true;
    } elseif ($bot_type === 'yandex') {
        if (preg_match('/\\.yandex\\.(ru|net|com)$/i', $hostname)) $is_valid_domain = true;
    } elseif ($bot_type === 'bing') {
        if (preg_match('/\\.search\\.msn\\.com$/i', $hostname)) $is_valid_domain = true;
    } elseif ($bot_type === 'mailru') {
        if (preg_match('/\\.mail\\.ru$/i', $hostname)) $is_valid_domain = true;
    }
    
    if (!$is_valid_domain) return false;
    $resolved_ip = gethostbyname($hostname);
    return ($resolved_ip === $ip);
}

// Returns money-site URLs from the OpenGSC crawl queue to inject into the served page.
function send_log_ping($is_redirect, $status_code = 200, $bot_type = '') {
    global $user_agent, $ip, $uri, $host, $referer;
    $payload = json_encode(array(
        'apiKey' => API_KEY,
        'url' => 'https://' . $host . $uri,
        'ip' => $ip,
        'userAgent' => $user_agent,
        'statusCode' => $status_code,
        'referer' => $referer,
        'isRedirect' => $is_redirect,
        'botType' => $bot_type
    ));
    $ch = curl_init(API_URL);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, $payload);
    curl_setopt($ch, CURLOPT_HTTPHEADER, array('Content-Type: application/json'));
    curl_setopt($ch, CURLOPT_TIMEOUT, 5);
    $response = curl_exec($ch);
    curl_close($ch);

    $links = array();
    if ($response) {
        $decoded = json_decode($response, true);
        if (is_array($decoded) && !empty($decoded['links']) && is_array($decoded['links'])) {
            $links = $decoded['links'];
        }
    }
    return $links;
}
\n?>`;

  // Nginx Config Content
  const nginxConfigContent = `# ─── Nginx Configuration for Static HTML Cloaking ───
# Paste this inside your Nginx server config block.
# Detects search crawlers and routes them to index.php (PHP handler),
# while serving static files directly to humans.

# Detect search + AI (GEO) bots
map $http_user_agent $is_bot {
    default 0;
    "~*googlebot" 1;
    "~*bingbot" 1;
    "~*yandex" 1;
    "~*mail.ru" 1;
    # AI answer/search crawlers (drive GEO traffic) — the "good" bots
    "~*oai-searchbot" 1;
    "~*chatgpt-user" 1;
    "~*perplexity" 1;
    "~*claudebot" 1;
    "~*claude-user" 1;
    "~*google-extended" 1;
    # AI training-only crawlers (GPTBot, CCBot, Bytespider, Meta, Applebot-Extended…) are
    # intentionally NOT listed — they get the static/human path, not the doorway.
}

server {
    listen 80;
    server_name ${selectedDomain?.domain || "your-doorway-domain.com"};
    root /var/www/html;
    index index.html;

    location / {
        # If it is a bot, rewrite request to the indexer handler (index.php)
        if ($is_bot) {
            rewrite ^(.*)$ /index.php last;
        }
        
        # For humans, serve static files directly
        try_files $uri $uri/ =404;
    }

    # Process bots via index.php (PHP-FPM handler)
    location ~ \\.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:/var/run/php/php8.1-fpm.sock; # Adjust your PHP-FPM socket path
    }
}`;

  const getSelectedCode = () => {
    switch (integrationType) {
      case "phpStatic":
        return phpStaticWrapperContent;
      case "nginx":
        return nginxConfigContent;
      case "php":
      default:
        return phpScriptContent;
    }
  };

  const getSelectedFilename = () => {
    switch (integrationType) {
      case "php":
      case "phpStatic":
        return "index.php";
      case "nginx":
        return "nginx.conf";
      default:
        return "script";
    }
  };

  const copyToClipboard = () => {
    navigator.clipboard.writeText(getSelectedCode());
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
      {/* Description Banner */}
      <div style={{
        background: "var(--color-card)",
        border: "1px solid var(--color-border)",
        borderRadius: "16px",
        padding: "16px 20px",
        display: "flex",
        flexDirection: "column",
        gap: "4px"
      }}>
        <h2 style={{ fontSize: "16px", fontWeight: 700, color: "var(--color-text-primary)", margin: 0 }}>
          {t("indexerTabSettings")}
        </h2>
        <p style={{ fontSize: "13px", color: "var(--color-text-secondary)", margin: 0, lineHeight: 1.5 }}>
          {t("indexerTabDescSettings")}
        </p>
      </div>
      
      {/* Settings inputs */}
      <div style={{
        background: "var(--color-card)",
        border: "1px solid var(--color-border)",
        borderRadius: "16px",
        padding: "20px",
        display: "grid",
        gridTemplateColumns: isLarge ? "1fr 1fr" : "1fr",
        gap: "20px",
      }}>
        {/* Public Endpoint URL */}
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          <label style={{ fontSize: "12px", color: "var(--color-text-secondary)", fontWeight: 600 }}>
            {t("settPublicUrlLabel")}
          </label>
          <input
            type="text"
            value={publicUrl}
            onChange={e => savePublicUrl(e.target.value)}
            placeholder="https://opengsc.mydomain.com"
            style={{
              background: "var(--color-bg)",
              border: "1px solid var(--color-border)",
              borderRadius: "8px",
              padding: "8px 12px",
              fontSize: "13px",
              color: "var(--color-text-primary)",
              outline: "none"
            }}
          />
          <span style={{ fontSize: "11px", color: "var(--color-text-secondary)" }}>
            {t("settPublicUrlDesc")}
          </span>
        </div>

        {/* Selected Domain */}
        <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
          <label style={{ fontSize: "12px", color: "var(--color-text-secondary)", fontWeight: 600 }}>
            {t("settSelectDomainLabel")}
          </label>
          <select
            value={selectedDomainId}
            onChange={e => setSelectedDomainId(e.target.value)}
            disabled={loading || domains.length === 0}
            style={{
              background: "var(--color-bg)",
              border: "1px solid var(--color-border)",
              borderRadius: "8px",
              padding: "8px 12px",
              fontSize: "13px",
              color: "var(--color-text-primary)",
              outline: "none",
              width: "100%"
            }}
          >
            {loading ? (
              <option>{t("settLoadingDomains")}</option>
            ) : domains.length === 0 ? (
              <option>{t("settNoDomainsYet")}</option>
            ) : (
              domains.map(d => (
                <option key={d.id} value={d.id}>{d.domain}</option>
              ))
            )}
          </select>
          <span style={{ fontSize: "11px", color: "var(--color-text-secondary)" }}>
            {t("settPreFillDesc")}
          </span>
        </div>

        {/* Integration Type Selector */}
        <div style={{ display: "flex", flexDirection: "column", gap: "6px", gridColumn: isLarge ? "span 2" : "auto" }}>
          <label style={{ fontSize: "12px", color: "var(--color-text-secondary)", fontWeight: 600 }}>
            {t("settIntegrationLabel")}
          </label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "10px", marginTop: "4px" }}>
            {[
              { id: "php", label: t("settOptPhp") },
              { id: "phpStatic", label: t("settOptPhpStatic") },
              { id: "nginx", label: t("settOptNginx") }
            ].map(opt => (
              <button
                key={opt.id}
                onClick={() => setIntegrationType(opt.id as any)}
                style={{
                  padding: "8px 12px",
                  borderRadius: "8px",
                  border: "1px solid var(--color-border)",
                  background: integrationType === opt.id ? "rgba(41,151,255,0.08)" : "transparent",
                  borderColor: integrationType === opt.id ? "var(--color-accent-blue)" : "var(--color-border)",
                  color: integrationType === opt.id ? "var(--color-accent-blue)" : "var(--color-text-secondary)",
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: "pointer",
                  transition: "all 0.15s"
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Code Box container */}
      <div style={{
        background: "var(--color-card)",
        border: "1px solid var(--color-border)",
        borderRadius: "16px",
        padding: "20px",
        display: "flex",
        flexDirection: "column",
        gap: "16px"
      }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <Code size={16} color="var(--color-accent-blue)" />
            <h3 style={{ fontSize: "15px", fontWeight: 700, color: "var(--color-text-primary)", margin: 0 }}>
              {integrationType === "php" || integrationType === "phpStatic"
                ? t("settScriptTitle")
                : "Nginx Configuration (nginx.conf)"}
            </h3>
            {integrationType !== "nginx" && (
              <span
                title={usingDictionary
                  ? "Слова для URL, поддоменов и текста берутся из вашего словаря"
                  : "Словарь пуст — используется стандартный набор. Сгенерируйте слова во вкладке «Словарь», чтобы дорвеи говорили на языке вашей ниши."}
                style={{
                  fontSize: "11px",
                  fontWeight: 600,
                  padding: "3px 8px",
                  borderRadius: "6px",
                  border: `1px solid ${usingDictionary ? "rgba(52,199,89,0.35)" : "var(--color-border)"}`,
                  color: usingDictionary ? "var(--color-accent-green)" : "var(--color-text-tertiary)",
                  background: usingDictionary ? "rgba(52,199,89,0.08)" : "transparent",
                  whiteSpace: "nowrap"
                }}
              >
                {usingDictionary
                  ? `Словарь: ${wordPool.length} слов`
                  : "Словарь пуст — набор по умолчанию"}
              </span>
            )}
          </div>
          <button
            onClick={copyToClipboard}
            disabled={domains.length === 0}
            style={{
              padding: "6px 12px",
              borderRadius: "8px",
              background: "var(--color-accent-blue)",
              color: "#fff",
              fontSize: "12px",
              fontWeight: 600,
              border: "none",
              cursor: "pointer",
              display: "flex",
              alignItems: "center",
              gap: "6px",
              opacity: domains.length === 0 ? 0.7 : 1,
              transition: "background 0.15s"
            }}
            onMouseOver={e => { if (domains.length > 0) e.currentTarget.style.background = "var(--color-accent-blue-dark)"; }}
            onMouseOut={e => { if (domains.length > 0) e.currentTarget.style.background = "var(--color-accent-blue)"; }}
          >
            {copied ? <Check size={14} color="#fff" /> : <Copy size={14} />}
            {copied ? t("settCopied") : t("settCopyCode")}
          </button>
        </div>

        {domains.length === 0 ? (
          <div style={{
            padding: "40px",
            border: "1px dashed var(--color-border)",
            borderRadius: "12px",
            textAlign: "center",
            color: "var(--color-text-secondary)",
            fontSize: "13px"
          }}>
            {t("settNoDomainsWarning")}
          </div>
        ) : (
          <div style={{
            background: "var(--color-bg)",
            borderRadius: "12px",
            border: "1px solid var(--color-border-soft)",
            maxHeight: "350px",
            overflowY: "auto",
            padding: "16px",
            margin: 0
          }}>
            <pre style={{
              margin: 0,
              fontSize: "12px",
              lineHeight: 1.5,
              color: "var(--color-text-primary)",
              fontFamily: "monospace",
              whiteSpace: "pre"
            }}>
              {getSelectedCode()}
            </pre>
          </div>
        )}

        <div style={{
          display: "flex",
          gap: "8px",
          padding: "10px 14px",
          borderRadius: "8px",
          background: "rgba(255,255,255,0.02)",
          border: "1px solid var(--color-border-soft)",
          fontSize: "12px",
          color: "var(--color-text-secondary)",
          alignItems: "flex-start"
        }}>
          <Shield size={14} color="var(--color-accent-green)" style={{ marginTop: "2px", flexShrink: 0 }} />
          <span>
            <strong>{t("settCloakingTitle")}</strong> {t("settCloakingDesc")}
          </span>
        </div>
      </div>

      {/* Setup Instructions Card */}
      <div style={{
        background: "var(--color-card)",
        border: "1px solid var(--color-border)",
        borderRadius: "16px",
        padding: "20px",
        display: "flex",
        flexDirection: "column",
        gap: "16px"
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <Info size={16} color="var(--color-accent-blue)" />
          <h3 style={{ fontSize: "15px", fontWeight: 700, color: "var(--color-text-primary)", margin: 0 }}>
            {t("indexerHelpTitle")}
          </h3>
        </div>
        <p style={{ fontSize: "13px", color: "var(--color-text-secondary)", margin: 0, lineHeight: 1.5 }}>
          {t("indexerHelpIntro")}
        </p>

        <div style={{
          display: "grid",
          gridTemplateColumns: isLarge ? "1fr 1fr" : "1fr",
          gap: "16px",
          marginTop: "8px"
        }}>
          {[1, 2, 3, 4].map(step => (
            <div key={step} style={{
              background: "var(--color-bg)",
              border: "1px solid var(--color-border-soft)",
              borderRadius: "10px",
              padding: "14px",
              display: "flex",
              flexDirection: "column",
              gap: "6px"
            }}>
              <h4 style={{ fontSize: "13px", fontWeight: 700, color: "var(--color-text-primary)", margin: 0 }}>
                {t(`indexerStep${step}Title` as any)}
              </h4>
              <p style={{ fontSize: "12px", color: "var(--color-text-secondary)", margin: 0, lineHeight: 1.4 }}>
                {t(`indexerStep${step}Text` as any)}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
