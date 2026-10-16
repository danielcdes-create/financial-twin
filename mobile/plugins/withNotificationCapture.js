const { withAndroidManifest } = require('expo/config-plugins');
module.exports = function withNotificationCapture(config) {
  return withAndroidManifest(config, config => {
    const app=config.modResults.manifest.application?.[0];
    if (!app) return config;
    app.$ = app.$ || {};
    const apiUrl = (process.env.EXPO_PUBLIC_API_BASE_URL || '').trim().toLowerCase();
    if (apiUrl.startsWith('http://')) app.$['android:usesCleartextTraffic'] = 'true';
    else delete app.$['android:usesCleartextTraffic'];
    app.service=app.service||[];
    const name='expo.modules.notificationcapture.NotificationCaptureService';
    if (!app.service.some(service=>service.$?.['android:name']===name)) {
      app.service.push({$:{'android:name':name,'android:label':'Financial Twin notification capture','android:permission':'android.permission.BIND_NOTIFICATION_LISTENER_SERVICE','android:exported':'true'},'intent-filter':[{'action':[{$:{'android:name':'android.service.notification.NotificationListenerService'}}]}]});
    }
    return config;
  });
};

