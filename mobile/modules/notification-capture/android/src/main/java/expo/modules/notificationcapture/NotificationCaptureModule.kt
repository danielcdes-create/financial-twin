package expo.modules.notificationcapture

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.provider.Settings
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.io.File

class NotificationCaptureModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("NotificationCapture")
    Function("isEnabled") { isListenerEnabled() }
    Function("openSettings") {
      appContext.reactContext?.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
    Function("getQueuedEvents") {
      val context=checkNotNull(appContext.reactContext){"Contexto nativo indisponível"}
      val snapshot=NotificationInbox.snapshot(context.getSharedPreferences(NotificationCaptureService.PREFS,Context.MODE_PRIVATE))
      check(snapshot.getString("state")!="unavailable"){snapshot.getString("error")}
      snapshot.getJSONArray("events").toString()
    }
    Function("getInboxSnapshot") {
      val context=checkNotNull(appContext.reactContext){"Contexto nativo indisponível"}
      NotificationInbox.snapshot(context.getSharedPreferences(NotificationCaptureService.PREFS,Context.MODE_PRIVATE)).toString()
    }
    Function("exportRecovery") {
      val context=checkNotNull(appContext.reactContext){"Contexto nativo indisponível"}
      val original=NotificationInbox.export(context.getSharedPreferences(NotificationCaptureService.PREFS,Context.MODE_PRIVATE))
      val folder=File(context.filesDir,"notification-recovery");check(folder.exists()||folder.mkdirs()){"Não foi possível preparar a recuperação"}
      val file=File(folder,"notifications-${System.currentTimeMillis()}.json");file.writeText(original,Charsets.UTF_8)
      check(file.readText(Charsets.UTF_8)==original){"A cópia de recuperação não foi confirmada"}
      android.net.Uri.fromFile(file).toString()
    }
    Function("markSynced") { eventId: String ->
      val context=checkNotNull(appContext.reactContext){"Contexto nativo indisponível"}
      NotificationInbox.markSynced(context.getSharedPreferences(NotificationCaptureService.PREFS,Context.MODE_PRIVATE),eventId)
    }
  }
  private fun isListenerEnabled(): Boolean {
    val context=appContext.reactContext ?: return false
    val enabled=Settings.Secure.getString(context.contentResolver,"enabled_notification_listeners") ?: return false
    return enabled.contains(context.packageName)
  }
}
