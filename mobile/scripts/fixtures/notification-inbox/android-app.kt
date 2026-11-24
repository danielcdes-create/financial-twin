package android.app
import android.os.Bundle
class Notification(val extras:Bundle?){
  companion object{
    const val EXTRA_TITLE="android.title"
    const val EXTRA_TEXT="android.text"
    const val EXTRA_BIG_TEXT="android.bigText"
  }
}
