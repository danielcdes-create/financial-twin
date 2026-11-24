package android.net
import java.io.File

class Uri(private val raw:String) {
  val scheme:String? get()=if(raw.contains(':'))raw.substringBefore(':') else null
  override fun toString():String=raw
  override fun equals(other:Any?):Boolean=other is Uri && raw==other.raw
  override fun hashCode():Int=raw.hashCode()
  companion object {
    fun fromFile(file:File):Uri=Uri(file.toURI().toString())
    fun parse(uriString:String):Uri=Uri(uriString)
  }
}
