const { withAndroidManifest, withAppBuildGradle } = require('expo/config-plugins');

const RELEASE_SIGNING_SNIPPET = `    def releaseConfigPath = findProperty('FT_RELEASE_CONFIG_FILE') ?: System.getenv('FT_RELEASE_CONFIG_FILE')
    def releaseConfigJson = null
    if (releaseConfigPath && file(releaseConfigPath).exists()) {
        releaseConfigJson = new groovy.json.JsonSlurper().parse(file(releaseConfigPath), 'UTF-8')
    }
    def releaseStorePath = System.getenv('FT_RELEASE_STORE_FILE') ?: findProperty('FT_RELEASE_STORE_FILE') ?: (releaseConfigJson ? releaseConfigJson.store_file : null)
    def releaseStorePass = System.getenv('FT_RELEASE_STORE_PASSWORD') ?: findProperty('FT_RELEASE_STORE_PASSWORD') ?: (releaseConfigJson ? releaseConfigJson.store_password : null)
    def releaseKeyAlias = System.getenv('FT_RELEASE_KEY_ALIAS') ?: findProperty('FT_RELEASE_KEY_ALIAS') ?: (releaseConfigJson ? releaseConfigJson.key_alias : null)
    def releaseKeyPass = System.getenv('FT_RELEASE_KEY_PASSWORD') ?: findProperty('FT_RELEASE_KEY_PASSWORD') ?: (releaseConfigJson ? releaseConfigJson.key_password : null)
    def hasPrivateReleaseKey = releaseStorePath && file(releaseStorePath).exists() && releaseStorePass && releaseKeyAlias && releaseKeyPass`;

module.exports = function withNotificationCapture(config) {
  config = withAndroidManifest(config, config => {
    const app = config.modResults.manifest.application?.[0];
    if (!app) return config;
    app.$ = app.$ || {};
    const apiUrl = (process.env.EXPO_PUBLIC_API_BASE_URL || '').trim().toLowerCase();
    if (apiUrl.startsWith('http://')) app.$['android:usesCleartextTraffic'] = 'true';
    else delete app.$['android:usesCleartextTraffic'];
    app.service = app.service || [];
    const name = 'expo.modules.notificationcapture.NotificationCaptureService';
    if (!app.service.some(service => service.$?.['android:name'] === name)) {
      app.service.push({
        $: {
          'android:name': name,
          'android:label': 'Financial Twin notification capture',
          'android:permission': 'android.permission.BIND_NOTIFICATION_LISTENER_SERVICE',
          'android:exported': 'true',
        },
        'intent-filter': [{ action: [{ $: { 'android:name': 'android.service.notification.NotificationListenerService' } }] }],
      });
    }
    return config;
  });

  config = withAppBuildGradle(config, config => {
    let contents = config.modResults.contents || '';
    if (!contents.includes('FT_RELEASE_CONFIG_FILE') && contents.includes('signingConfigs {')) {
      contents = contents.replace(
        '    signingConfigs {',
        `${RELEASE_SIGNING_SNIPPET}\n\n    signingConfigs {`
      );
      contents = contents.replace(
        /signingConfigs\s*\{\s*debug\s*\{([\s\S]*?)\}/m,
        match => `${match}\n        release {\n            if (hasPrivateReleaseKey) {\n                storeFile file(releaseStorePath)\n                storePassword releaseStorePass\n                keyAlias releaseKeyAlias\n                keyPassword releaseKeyPass\n            } else {\n                storeFile file('debug.keystore')\n                storePassword 'android'\n                keyAlias 'androiddebugkey'\n                keyPassword 'android'\n            }\n        }`
      );
      contents = contents.replace(
        /release\s*\{\s*\/\/ Caution![\s\S]*?signingConfig signingConfigs\.debug/m,
        'release {\n            signingConfig hasPrivateReleaseKey ? signingConfigs.release : signingConfigs.debug'
      );
      config.modResults.contents = contents;
    }
    return config;
  });

  return config;
};
