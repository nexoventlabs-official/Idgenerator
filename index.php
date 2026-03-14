<?php
/**
 * PHP Reverse Proxy to Gunicorn (Flask App)
 * Routes all requests through the existing Cloudways stack to Gunicorn on port 8000
 * 
 * IMPORTANT: Cloudways Varnish strips cookies from GET requests.
 * This proxy uses a file-based cookie jar to persist Flask session cookies
 * across requests, bypassing Varnish's cookie stripping.
 */

$gunicorn_host = '127.0.0.1';
$gunicorn_port = 8000;
$cookie_jar_dir = __DIR__ . '/data/cookie_jars';

// Ensure cookie jar directory exists
if (!is_dir($cookie_jar_dir)) {
    mkdir($cookie_jar_dir, 0700, true);
}

// Build the target URL
$path = $_SERVER['REQUEST_URI'];
$url = "http://{$gunicorn_host}:{$gunicorn_port}{$path}";

// Get request method and headers
$method = $_SERVER['REQUEST_METHOD'];

// Headers to skip — these are managed by cURL or added explicitly below
$skip_headers = ['host', 'connection', 'transfer-encoding', 'content-length', 'content-type', 'cookie',
                 'x-forwarded-for', 'x-forwarded-proto', 'x-real-ip'];

// Build headers to forward
$headers = [];
foreach ($_SERVER as $key => $value) {
    if (strpos($key, 'HTTP_') === 0) {
        $header = str_replace('_', '-', substr($key, 5));
        if (!in_array(strtolower($header), $skip_headers)) {
            $headers[] = "{$header}: {$value}";
        }
    }
}

// Always force HTTPS proto (Cloudways external traffic is always HTTPS)
$headers[] = "Host: " . $_SERVER['HTTP_HOST'];
$headers[] = "X-Forwarded-For: " . ($_SERVER['REMOTE_ADDR'] ?? '127.0.0.1');
$headers[] = "X-Forwarded-Proto: https";
$headers[] = "X-Real-IP: " . ($_SERVER['REMOTE_ADDR'] ?? '127.0.0.1');

// Cookie handling: Varnish strips cookies on GET requests
// Use a cookie jar file keyed by client identity to persist session cookies
$client_id = md5(($_SERVER['REMOTE_ADDR'] ?? '') . '|' . ($_SERVER['HTTP_USER_AGENT'] ?? ''));
$cookie_jar_file = $cookie_jar_dir . '/' . $client_id . '.txt';

// Collect cookies: from browser (if Varnish passed them) + from cookie jar
$browser_cookies = isset($_SERVER['HTTP_COOKIE']) ? $_SERVER['HTTP_COOKIE'] : '';
$jar_cookies = '';

if (file_exists($cookie_jar_file) && (time() - filemtime($cookie_jar_file)) < 86400) {
    $jar_data = file_get_contents($cookie_jar_file);
    if ($jar_data) {
        $jar_cookies = trim($jar_data);
    }
}

// Merge cookies: browser cookies take precedence
$merged_cookies = $jar_cookies;
if ($browser_cookies) {
    // Parse both into arrays, browser wins on conflict
    $jar_parsed = [];
    $browser_parsed = [];
    
    foreach (explode(';', $jar_cookies) as $c) {
        $c = trim($c);
        if ($c && strpos($c, '=') !== false) {
            list($name, $val) = explode('=', $c, 2);
            $jar_parsed[trim($name)] = trim($val);
        }
    }
    foreach (explode(';', $browser_cookies) as $c) {
        $c = trim($c);
        if ($c && strpos($c, '=') !== false) {
            list($name, $val) = explode('=', $c, 2);
            $browser_parsed[trim($name)] = trim($val);
        }
    }
    
    $all_cookies = array_merge($jar_parsed, $browser_parsed);
    $parts = [];
    foreach ($all_cookies as $name => $val) {
        $parts[] = "{$name}={$val}";
    }
    $merged_cookies = implode('; ', $parts);
}

if ($merged_cookies) {
    $headers[] = "Cookie: " . $merged_cookies;
}

// Forward Content-Type if present
$content_type = $_SERVER['CONTENT_TYPE'] ?? ($_SERVER['HTTP_CONTENT_TYPE'] ?? '');
if ($content_type) {
    $headers[] = "Content-Type: {$content_type}";
}

// Initialize cURL
$ch = curl_init();
curl_setopt($ch, CURLOPT_URL, $url);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_HEADER, true);
curl_setopt($ch, CURLOPT_FOLLOWLOCATION, false);
curl_setopt($ch, CURLOPT_TIMEOUT, 120);
curl_setopt($ch, CURLOPT_CONNECTTIMEOUT, 10);

// Handle different HTTP methods
$raw = file_get_contents('php://input');

switch ($method) {
    case 'POST':
        curl_setopt($ch, CURLOPT_POST, true);
        if (strpos($content_type, 'multipart/form-data') !== false) {
            $postData = [];
            foreach ($_FILES as $key => $file) {
                if (is_array($file['tmp_name'])) {
                    foreach ($file['tmp_name'] as $i => $tmp) {
                        $postData["{$key}[{$i}]"] = new CURLFile($tmp, $file['type'][$i], $file['name'][$i]);
                    }
                } else {
                    $postData[$key] = new CURLFile($file['tmp_name'], $file['type'], $file['name']);
                }
            }
            foreach ($_POST as $key => $value) {
                $postData[$key] = $value;
            }
            curl_setopt($ch, CURLOPT_POSTFIELDS, $postData);
        } else {
            curl_setopt($ch, CURLOPT_POSTFIELDS, $raw);
        }
        break;
    case 'PUT':
    case 'PATCH':
    case 'DELETE':
        curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
        if ($raw) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, $raw);
        }
        break;
}

// Set headers AFTER body so cURL can compute Content-Length automatically
curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);

// Execute request
$response = curl_exec($ch);

if (curl_errno($ch)) {
    http_response_code(502);
    echo "Bad Gateway: Flask application is not responding. Error: " . curl_error($ch);
    curl_close($ch);
    exit;
}

$header_size = curl_getinfo($ch, CURLINFO_HEADER_SIZE);
$status_code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
$response_headers = substr($response, 0, $header_size);
$body = substr($response, $header_size);

curl_close($ch);

// Set response status code
http_response_code($status_code);

// Forward response headers and capture Set-Cookie for the cookie jar
$header_lines = explode("\r\n", $response_headers);
$new_cookies = [];

foreach ($header_lines as $header_line) {
    if (empty($header_line) || strpos($header_line, 'HTTP/') === 0) continue;
    $lower = strtolower($header_line);
    if (strpos($lower, 'transfer-encoding') === 0) continue;
    if (strpos($lower, 'connection') === 0) continue;
    
    // Capture Set-Cookie headers to save in cookie jar
    if (strpos($lower, 'set-cookie:') === 0) {
        $cookie_value = trim(substr($header_line, 11));
        // Extract just the name=value part (before ;)
        $parts = explode(';', $cookie_value);
        $name_val = trim($parts[0]);
        if (strpos($name_val, '=') !== false) {
            list($cname, $cval) = explode('=', $name_val, 2);
            $new_cookies[trim($cname)] = trim($cval);
        }
    }
    
    header($header_line, false);
}

// Update cookie jar with any new/changed cookies from the response
if (!empty($new_cookies)) {
    // Load existing jar cookies
    $existing = [];
    if ($jar_cookies) {
        foreach (explode(';', $jar_cookies) as $c) {
            $c = trim($c);
            if ($c && strpos($c, '=') !== false) {
                list($name, $val) = explode('=', $c, 2);
                $existing[trim($name)] = trim($val);
            }
        }
    }
    
    // Merge new cookies into jar
    $all = array_merge($existing, $new_cookies);
    $parts = [];
    foreach ($all as $name => $val) {
        $parts[] = "{$name}={$val}";
    }
    file_put_contents($cookie_jar_file, implode('; ', $parts));
}

// Output response body
echo $body;
