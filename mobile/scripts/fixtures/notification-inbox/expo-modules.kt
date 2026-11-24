package expo.modules.kotlin.modules
import android.content.Context

class AppContext(var reactContext:Context?=null)

class ModuleDefinitionData(
  val name:String,
  val functions:Map<String,(Array<out Any?>)->Any?>
) {
  fun invoke(fnName:String,vararg args:Any?):Any? {
    val fn=checkNotNull(functions[fnName]){"Registered Expo module function not found: $fnName"}
    return fn(args)
  }
}

class ModuleDefinitionBuilder {
  var moduleName:String=""
  @PublishedApi internal val functions=linkedMapOf<String,(Array<out Any?>)->Any?>()
  fun Name(name:String){moduleName=name}
  @JvmName("FunctionWithoutArgs")
  inline fun Function(name:String,crossinline body:()->Any?){
    functions[name]={_->body()}
  }
  inline fun <reified R,reified P0> Function(name:String,crossinline body:(p0:P0)->R){
    functions[name]={args->body(args[0] as P0)}
  }
  inline fun <reified R,reified P0,reified P1> Function(name:String,crossinline body:(p0:P0,p1:P1)->R){
    functions[name]={args->body(args[0] as P0,args[1] as P1)}
  }
  @JvmName("AsyncFunctionWithoutArgs")
  inline fun AsyncFunction(name:String,crossinline body:()->Any?)=Function(name,body)
  inline fun <reified R,reified P0> AsyncFunction(name:String,crossinline body:(p0:P0)->R)=Function<R,P0>(name,body)
  inline fun <reified R,reified P0,reified P1> AsyncFunction(name:String,crossinline body:(p0:P0,p1:P1)->R)=Function<R,P0,P1>(name,body)
  fun build()=ModuleDefinitionData(moduleName,functions.toMap())
}

inline fun ModuleDefinition(block:ModuleDefinitionBuilder.()->Unit):ModuleDefinitionData {
  val builder=ModuleDefinitionBuilder()
  builder.block()
  return builder.build()
}

abstract class Module {
  val appContext=AppContext()
  abstract fun definition():ModuleDefinitionData
}
