<?php
header('Content-Type: text/plain');
echo "=== COOKIES ===\n";
echo isset($_SERVER['HTTP_COOKIE']) ? $_SERVER['HTTP_COOKIE'] : 'NO_COOKIES';
echo "\n=== HTTPS ===\n";
echo isset($_SERVER['HTTPS']) ? $_SERVER['HTTPS'] : 'NOT_SET';
echo "\n=== SERVER_PORT ===\n";
echo isset($_SERVER['SERVER_PORT']) ? $_SERVER['SERVER_PORT'] : 'NOT_SET';
echo "\n=== X-FORWARDED-PROTO ===\n";
echo isset($_SERVER['HTTP_X_FORWARDED_PROTO']) ? $_SERVER['HTTP_X_FORWARDED_PROTO'] : 'NOT_SET';
echo "\n=== REAL SCHEME ===\n";
echo isset($_SERVER['HTTP_X_REAL_SCHEME']) ? $_SERVER['HTTP_X_REAL_SCHEME'] : 'NOT_SET';
echo "\n";
