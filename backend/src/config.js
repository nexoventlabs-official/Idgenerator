require('dotenv').config();

const config = {
  port:    process.env.PORT    || 5000,
  nodeEnv: process.env.NODE_ENV || 'development',

  // ── DB2: App data (Atlas) — writes happen here ──────────────────
  mongoUri: process.env.MONGO_URI || '',
  mongoDb:  process.env.MONGO_DB  || 'wetheleaders',

  // ── DB1: Voter roll (DigitalOcean) — READ-ONLY ──────────────────
  mongoVoterUrl:    process.env.MONGO_VOTER_URL    || '',
  mongoVoterDbName: process.env.MONGO_VOTER_DB_NAME || 'voter_db',

  cloudinary: {
    cloudName:   process.env.CLOUDINARY_CLOUD_NAME  || '',
    apiKey:      process.env.CLOUDINARY_API_KEY      || '',
    apiSecret:   process.env.CLOUDINARY_API_SECRET   || '',
    photoFolder: process.env.CLOUDINARY_PHOTO_FOLDER || 'member_photos',
    cardsFolder: process.env.CLOUDINARY_CARDS_FOLDER || 'generated_cards',
  },

  admin: {
    username: process.env.ADMIN_USERNAME || '',
    password: process.env.ADMIN_PASSWORD || '',
  },

  smsApiKey:          process.env.SMS_API_KEY          || '',
  whatsappChannelUrl: process.env.WHATSAPP_CHANNEL_URL || '',
  baseUrl:            process.env.BASE_URL              || 'http://localhost:5000',
  sessionSecret:      process.env.SESSION_SECRET        || 'wtl-session-secret',
};

if (!config.admin.username || !config.admin.password) {
  throw new Error('ADMIN_USERNAME and ADMIN_PASSWORD must be set in .env');
}

module.exports = config;
