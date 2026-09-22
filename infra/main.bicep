targetScope = 'resourceGroup'

@description('Region verified against current real-time avatar documentation.')
@allowed(['westeurope', 'swedencentral', 'northeurope', 'westus2', 'eastus'])
param location string = 'westeurope'

@secure()
param operatorTokenHash string

@secure()
param sessionSigningKey string

param sourceCommit string

var suffix = uniqueString(resourceGroup().id, 'changeguard-v1')
var speechName = 'cg-speech-${suffix}'
var appName = 'cg-web-${suffix}'
var tags = {
  application: 'changeguard'
  purpose: 'bounded-avatar-reference'
  approval: 'human-review-pending'
}

resource speech 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: speechName
  location: location
  kind: 'SpeechServices'
  sku: {
    name: 'S0'
  }
  tags: tags
  properties: {
    customSubDomainName: speechName
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'cg-plan-${suffix}'
  location: location
  kind: 'linux'
  tags: tags
  sku: {
    name: 'B1'
    tier: 'Basic'
    capacity: 1
  }
  properties: {
    reserved: true
  }
}

resource web 'Microsoft.Web/sites@2024-04-01' = {
  name: appName
  location: location
  kind: 'app,linux'
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: false
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      appCommandLine: 'gunicorn --config gunicorn.conf.py app:app'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      scmMinTlsVersion: '1.2'
      http20Enabled: true
      healthCheckPath: '/api/health'
      numberOfWorkers: 1
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'ENABLE_ORYX_BUILD', value: 'true' }
        { name: 'CHANGEGUARD_MODE', value: 'live-azure' }
        { name: 'CHANGEGUARD_DATA_DIR', value: '/home/changeguard-data' }
        { name: 'PUBLIC_ORIGIN', value: 'https://${appName}.azurewebsites.net' }
        { name: 'OPERATOR_TOKEN_SHA256', value: operatorTokenHash }
        { name: 'SESSION_SIGNING_KEY', value: sessionSigningKey }
        { name: 'SPEECH_RESOURCE_ID', value: speech.id }
        { name: 'SPEECH_HOST', value: '${speechName}.cognitiveservices.azure.com' }
        { name: 'SPEECH_REGION', value: location }
        { name: 'SOURCE_COMMIT', value: sourceCommit }
        { name: 'PYTHONUNBUFFERED', value: '1' }
      ]
    }
  }
}

resource scmAuth 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-04-01' = {
  parent: web
  name: 'scm'
  properties: {
    allow: false
  }
}

resource ftpAuth 'Microsoft.Web/sites/basicPublishingCredentialsPolicies@2024-04-01' = {
  parent: web
  name: 'ftp'
  properties: {
    allow: false
  }
}

resource speechUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(speech.id, web.id, 'speech-user')
  scope: speech
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'f2dc8367-1007-4938-bd23-fe263f013447'
    )
    principalId: web.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

output appName string = web.name
output speechName string = speech.name
output planName string = plan.name
output url string = 'https://${web.properties.defaultHostName}'
output region string = location
