const BASE_URL = 'http://127.0.0.1:8000/api'

function getAuthHeaders() {
  const token = localStorage.getItem('authToken')
  return token ? { 'Authorization': `Token ${token}` } : {}
}

export async function registerUser({ username, password, email }) {
  const response = await fetch(`${BASE_URL}/auth/register/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, email }),
  })

  const data = await response.json()
  if (!response.ok) {
    throw new Error(data.error || 'Registration failed')
  }
  return data
}

export async function loginUser({ username, password }) {
  const response = await fetch(`${BASE_URL}/auth/login/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })

  const data = await response.json()
  if (!response.ok) {
    throw new Error(data.error || 'Login failed')
  }
  return data
}

export function logoutUser() {
  localStorage.removeItem('authToken')
  localStorage.removeItem('username')
}

export async function createCampaign({ name, messageText, sendMode, contacts, mediaFiles }) {
  const formData = new FormData()
  formData.append('name', name)
  formData.append('message_text', messageText)
  formData.append('send_mode', sendMode)
  formData.append('contacts', JSON.stringify(contacts))

  mediaFiles.forEach((file) => {
    formData.append('media_files', file)
  })

  const response = await fetch(`${BASE_URL}/campaigns/create/`, {
    method: 'POST',
    headers: getAuthHeaders(),
    body: formData,
  })

  if (!response.ok) {
    throw new Error('Failed to create campaign')
  }

  return response.json()
}

export async function startSending(campaignId) {
  const response = await fetch(`${BASE_URL}/campaigns/${campaignId}/start/`, {
    method: 'POST',
    headers: getAuthHeaders(),
  })

  if (!response.ok) {
    throw new Error('Failed to start sending')
  }

  return response.json()
}

export async function getCampaignStatus(campaignId) {
  const response = await fetch(`${BASE_URL}/campaigns/${campaignId}/status/`, {
    headers: getAuthHeaders(),
  })

  if (!response.ok) {
    throw new Error('Failed to fetch campaign status')
  }

  return response.json()
}