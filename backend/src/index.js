/**
 * We The Leaders — Express API Server
 * =====================================
 * Node.js port of Flask app.py
 * Lead the Change
 */
require('dotenv').config();

const express      = require('express');
const session      = require('express-session');
const MongoStore   = require('connect-mongo');
const cors         = require('cors');
const helmet       = require('helmet');
const path         = require('path');
const config       = require('./config');
const { connectDB } = require('./db');

// ── Route modules ─────────────────────────────────────────────────
const chatRoutes   = require('./routes/chat');
const adminRoutes  = require('./routes/admin');
const publicRoutes = require('./routes/public');

const app = express();

// ── Security headers (mirrors Flask's set_security_headers) ───────
app.use(helmet({
  contentSecurityPolicy: config.nodeEnv === 'production' ? {
    directives: {
      defaultSrc: ["'self'", 'https://res.cloudinary.com'],
      imgSrc:     ["'self'", 'https://res.cloudinary.com', 'data:'],
      styleSrc:   ["'self'", "'unsafe-inline'", 'https://fonts.googleapis.com', 'https://cdn.jsdelivr.net'],
      scriptSrc:  ["'self'", "'unsafe-inline'", 'https://cdn.jsdelivr.net'],
      fontSrc:    ["'self'", 'https://fonts.gstatic.com', 'https://cdn.jsdelivr.net'],
      connectSrc: ["'self'", 'https://cdn.jsdelivr.net'],
    },
  } : false,
  crossOriginResourcePolicy: { policy: 'cross-origin' },
}));

// Custom security headers matching Python's after_request hook
app.use((req, res, next) => {
  res.setHeader('X-Content-Type-Options',  'nosniff');
  res.setHeader('X-Frame-Options',          'DENY');
  res.setHeader('X-XSS-Protection',         '1; mode=block');
  res.setHeader('Referrer-Policy',          'strict-origin-when-cross-origin');
  res.setHeader('Permissions-Policy',       'geolocation=(), microphone=(), camera=()');

  if (req.path.startsWith('/static/')) {
    res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
  } else if (req.path.startsWith('/admin') || req.path.startsWith('/api/')) {
    res.setHeader('Cache-Control', 'no-store, no-cache, must-revalidate, private, max-age=0');
    res.setHeader('Pragma',  'no-cache');
    res.setHeader('Expires', '0');
  }
  next();
});

// ── CORS ──────────────────────────────────────────────────────────
const allowedOrigins = config.nodeEnv === 'development'
  ? ['http://localhost:3000', 'http://localhost:5173', 'http://127.0.0.1:3000']
  : [config.baseUrl];

app.use(cors({
  origin: (origin, cb) => {
    if (!origin || allowedOrigins.includes(origin)) return cb(null, true);
    cb(null, true); // be permissive in dev; tighten in production as needed
  },
  credentials: true,
  methods:     ['GET', 'POST', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization'],
}));

// ── Body parsers ──────────────────────────────────────────────────
app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true, limit: '10mb' }));

// ── Sessions with MongoDB store ───────────────────────────────────
app.use(session({
  secret:            config.sessionSecret,
  resave:            false,
  saveUninitialized: false,
  store: MongoStore.create({
    mongoUrl:       config.mongoUri,
    dbName:         config.mongoDb,
    collectionName: 'sessions',
    ttl:            86400, // 1 day in seconds
    autoRemove:     'native',
  }),
  cookie: {
    httpOnly: true,
    sameSite: 'lax',
    secure:   config.nodeEnv === 'production',
    maxAge:   86400 * 1000, // 1 day in ms
  },
  name: 'wtl.session',
}));

// ── Static files ──────────────────────────────────────────────────
const frontendDist = path.join(__dirname, '../../frontend/dist');
const staticDir    = path.join(__dirname, '../../../static');

if (require('fs').existsSync(frontendDist) && config.nodeEnv === 'production') {
  app.use(express.static(frontendDist, { maxAge: '1y', etag: true }));
}

// Serve Python app's static folder if present (banner.jpg, favicon, etc.)
if (require('fs').existsSync(staticDir)) {
  app.use('/static', express.static(staticDir, { maxAge: '7d' }));
}

// ── API Routes ────────────────────────────────────────────────────
app.use('/api',      chatRoutes);   // /api/send-otp, /api/validate-epic, etc.
app.use('/admin',    adminRoutes);
app.use('/',         publicRoutes);

// ── SPA fallback (production) ────────────────────────────────────
if (config.nodeEnv === 'production' && require('fs').existsSync(frontendDist)) {
  const indexHtml = path.join(frontendDist, 'index.html');
  app.get('*', (req, res) => {
    // Don't serve SPA for API or admin paths
    if (req.path.startsWith('/api/') || req.path.startsWith('/admin/')) {
      return res.status(404).json({ success: false, message: 'Not found' });
    }
    res.sendFile(indexHtml);
  });
}

// ── 404 fallback ─────────────────────────────────────────────────
app.use((req, res) => {
  res.status(404).json({ success: false, message: 'Route not found' });
});

// ── Global error handler ──────────────────────────────────────────
app.use((err, req, res, _next) => {
  console.error('Unhandled error:', err);
  res.status(500).json({ success: false, message: 'Internal server error' });
});

// ── Start server ─────────────────────────────────────────────────
async function startServer() {
  await connectDB();

  app.listen(config.port, () => {
    console.log('─────────────────────────────────────────');
    console.log('  WE THE LEADERS — Lead the Change');
    console.log(`  API server running on port ${config.port}`);
    console.log(`  Environment : ${config.nodeEnv}`);
    console.log(`  Base URL    : ${config.baseUrl}`);
    console.log('─────────────────────────────────────────');
  });
}

startServer().catch((err) => {
  console.error('Failed to start server:', err);
  process.exit(1);
});

module.exports = app;
