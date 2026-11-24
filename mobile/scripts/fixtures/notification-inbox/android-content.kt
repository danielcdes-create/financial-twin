package android.content
import android.net.Uri
import java.io.File

interface SharedPreferences {
  fun getString(key:String,defaultValue:String?):String?
  fun edit():Editor
  interface Editor {fun putString(key:String,value:String):Editor;fun commit():Boolean}
}

class ContentResolver {
  val settings=mutableMapOf<String,String?>()
}

open class Context(
  open var packageName:String="com.financialtwin.app",
  open var filesDir:File=File(System.getProperty("java.io.tmpdir"),"fin-ctx-files"),
  open var prefs:SharedPreferences?=null
) {
  val contentResolver=ContentResolver()
  val startedIntents=mutableListOf<Intent>()
  open fun getSharedPreferences(name:String,mode:Int):SharedPreferences=checkNotNull(prefs){"FakePrefs not attached to Context"}
  open fun startActivity(intent:Intent){startedIntents.add(intent)}
  companion object {
    const val MODE_PRIVATE=0
  }
}

class ComponentName(val packageName:String,val className:String) {
  constructor(pkg:Context,cls:Class<*>):this(pkg.packageName,cls.name)
  fun flattenToString():String="$packageName/$className"
  fun flattenToShortString():String=if(className.startsWith("$packageName."))"$packageName/${className.removePrefix(packageName)}" else flattenToString()
  override fun equals(other:Any?):Boolean=other is ComponentName && packageName==other.packageName && className==other.className
  override fun hashCode():Int=31*packageName.hashCode()+className.hashCode()
  companion object {
    fun unflattenFromString(str:String):ComponentName? {
      val sep=str.indexOf('/')
      if(sep<=0 || sep+1>=str.length)return null
      val pkg=str.substring(0,sep)
      var cls=str.substring(sep+1)
      if(cls.startsWith("."))cls=pkg+cls
      return ComponentName(pkg,cls)
    }
  }
}

class Intent(var action:String?=null,var data:Uri?=null) {
  var flags:Int=0
  var type:String?=null
  fun addFlags(extraFlags:Int):Intent{flags=flags or extraFlags;return this}
  fun setFlags(newFlags:Int):Intent{flags=newFlags;return this}
  fun setDataAndType(uri:Uri?,mimeType:String?):Intent{data=uri;type=mimeType;return this}
  fun setType(mimeType:String?):Intent{type=mimeType;return this}
  fun setData(uri:Uri?):Intent{data=uri;return this}
  companion object {
    const val ACTION_VIEW="android.intent.action.VIEW"
    const val FLAG_ACTIVITY_NEW_TASK=0x10000000
    const val FLAG_GRANT_READ_URI_PERMISSION=0x00000001
  }
}
