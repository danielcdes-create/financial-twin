package android.os
class SpannableStub(private val text:String):CharSequence {
  override val length:Int get()=text.length
  override fun get(index:Int):Char=text[index]
  override fun subSequence(startIndex:Int,endIndex:Int):CharSequence=SpannableStub(text.substring(startIndex,endIndex))
  override fun toString():String=text
}
class Bundle(val values:Map<String,CharSequence> = emptyMap()){
  fun getCharSequence(key:String):CharSequence?=values[key]
  fun getString(key:String):String?=values[key]?.toString()
}
