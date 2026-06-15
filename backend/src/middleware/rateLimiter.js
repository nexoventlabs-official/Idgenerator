const rateLimit = require('express-rate-limit');

/**
 * Factory for creating rate limiters.
 * @param {number} maxRequests - max requests in window
 * @param {number} windowSeconds - window duration in seconds
 */
function createRateLimiter(maxRequests, windowSeconds) {
  return rateLimit({
    windowMs: windowSeconds * 1000,
    max: maxRequests,
    standardHeaders: true,
    legacyHeaders: false,
    handler: (req, res) => {
      res.status(429).json({
        success: false,
        message: `Rate limit exceeded. Try again in ${windowSeconds} seconds.`,
      });
    },
  });
}

const adminLoginLimiter      = createRateLimiter(5,  15 * 60); // 5 per 15 min
const chatOtpLimiter         = createRateLimiter(3,  5  * 60); // 3 per 5 min
const chatGenerateCardLimiter= createRateLimiter(5,  5  * 60); // 5 per 5 min
const chatValidateEpicLimiter= createRateLimiter(10, 60);       // 10 per 60 s

module.exports = {
  createRateLimiter,
  adminLoginLimiter,
  chatOtpLimiter,
  chatGenerateCardLimiter,
  chatValidateEpicLimiter,
};
