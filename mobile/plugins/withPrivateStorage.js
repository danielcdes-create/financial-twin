const { withAndroidManifest } = require('expo/config-plugins');

module.exports = config => withAndroidManifest(config, config => {
  const application = config.modResults.manifest.application?.[0];
  if (application) {
    application.$['android:allowBackup'] = 'false';
    application.$['android:fullBackupContent'] = 'false';
  }
  return config;
});
