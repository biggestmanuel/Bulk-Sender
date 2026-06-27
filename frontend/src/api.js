const BASE_URL = 'http://127.0.0.1:8000/api'

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
  })

  if (!response.ok) {
    throw new Error('Failed to start sending')
  }

  return response.json()
}

export async function getCampaignStatus(campaignId) {
  const response = await fetch(`${BASE_URL}/campaigns/${campaignId}/status/`)

  if (!response.ok) {
    throw new Error('Failed to fetch campaign status')
  }

  return response.json()
}