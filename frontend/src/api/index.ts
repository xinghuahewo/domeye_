// Keep the existing VITE_API_URL compatibility strategy.
// Business pages should call `request` with relative paths like `login`
// or `events/top`, without manually repeating `/api/v1`.
const baseUrl = import.meta.env.VITE_API_URL || '/api/v1/'

export default baseUrl
