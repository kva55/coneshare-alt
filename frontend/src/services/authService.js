import api from './api';

// Tokens are httpOnly cookies managed by the backend; nothing is stored in the browser
// by JavaScript.
const login = async (email, password) => {
  const response = await api.post('/token/', { email, password });
  return response.data.user;
};

const logout = async () => {
  try {
    await api.post('/token/logout/');
  } catch (error) {
    // Log the error but proceed: the UI session is over either way.
    console.error('Logout failed:', error);
  }
};

const getPublicSettings = async () => {
  const response = await api.get('/public/settings/');
  return response.data;
};

export const authService = {
  login,
  logout,
  getPublicSettings,
};
