package android.service.notification
import android.app.Notification
import android.content.SharedPreferences
class StatusBarNotification(val packageName:String?,val notification:Notification?,val key:String,val postTime:Long)
class FakePrefs:SharedPreferences {
  val values=mutableMapOf<String,String>();var commitOk=true;var readOk=true
  override fun getString(key:String,defaultValue:String?):String? {check(readOk){"Injected read failure"};return values[key]?:defaultValue}
  override fun edit()=Editor(this)
  class Editor(val prefs:FakePrefs):SharedPreferences.Editor{
    val changes=mutableMapOf<String,String>()
    override fun putString(key:String,value:String):Editor{changes[key]=value;return this}
    override fun commit():Boolean{if(prefs.commitOk)prefs.values.putAll(changes);return prefs.commitOk}
  }
}
open class NotificationListenerService {
  val prefs=FakePrefs()
  fun getSharedPreferences(name:String,mode:Int):SharedPreferences=prefs
  open fun onNotificationPosted(sbn:StatusBarNotification){}
  companion object {const val MODE_PRIVATE=0}
}
