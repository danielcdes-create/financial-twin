package android.provider
import android.content.ContentResolver

object Settings {
  const val ACTION_NOTIFICATION_LISTENER_SETTINGS="android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS"
  object Secure {
    const val ENABLED_NOTIFICATION_LISTENER_KEY="enabled_notification_listeners"
    fun getString(resolver:ContentResolver?,name:String):String?=resolver?.settings?.get(name)
    fun putString(resolver:ContentResolver,name:String,value:String?){resolver.settings[name]=value}
  }
}
