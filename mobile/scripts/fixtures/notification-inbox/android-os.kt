package android.os
class Bundle(val values:Map<String,String>){fun getCharSequence(key:String):CharSequence?=values[key]}
