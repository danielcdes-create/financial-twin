package expo.modules.notificationcapture

import android.content.SharedPreferences
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import java.text.SimpleDateFormat
import java.util.Locale

/** Versioned inbox. Damaged source bytes are retained before replacing a projection. */
object NotificationInbox {
  const val KEY_INBOX="inbox_v2"
  const val KEY_RECOVERY="recovery_v2"
  @Volatile private var failure:String?=null

  private fun valid(value:Any?):Boolean {
    if(value !is JSONObject)return false
    val id=value.opt("event_id")
    if(id !is String || runCatching{UUID.fromString(id)}.isFailure)return false
    val captured=value.opt("captured_at")
    if(captured !is String || !Regex("^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?Z$").matches(captured))return false
    if(runCatching{SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss",Locale.US).apply{isLenient=false}.parse(captured.substring(0,19))}.isFailure)return false
    return value.opt("source_type")=="android_notification" && value.opt("source_provider") is String && value.optString("source_provider").isNotBlank() &&
      value.opt("raw_payload") is JSONObject && (value.opt("parsed_payload")==null || value.opt("parsed_payload") is JSONObject)
  }
  private fun write(prefs:SharedPreferences,events:JSONArray,recovery:JSONArray?=null) {
    val edit=prefs.edit().putString(KEY_INBOX,JSONObject().put("version",2).put("events",events).toString())
    if(recovery!=null)edit.putString(KEY_RECOVERY,recovery.toString())
    check(edit.commit()){ "Falha ao persistir a fila de notificações. A origem permanece preservada." }
    failure=null
  }
  private fun read(prefs:SharedPreferences):Pair<JSONArray,JSONArray> {
    val recoveryRaw=prefs.getString(KEY_RECOVERY,"[]")?:"[]"
    var damagedRecovery=false
    val recoverySource=try{JSONArray(recoveryRaw)}catch(_:Exception){damagedRecovery=true;JSONArray()}
    val recovery=JSONArray()
    for(i in 0 until recoverySource.length()){
      val item=recoverySource.opt(i)
      if(item is JSONObject && item.opt("source_key") is String && item.opt("raw") is String)recovery.put(item) else damagedRecovery=true
    }
    if(damagedRecovery)recovery.put(JSONObject().put("source_key",KEY_RECOVERY).put("raw",recoveryRaw).put("reason","invalid_recovery_archive").put("recorded_at_ms",System.currentTimeMillis()))
    val current=prefs.getString(KEY_INBOX,null)
    val raw=current?:prefs.getString(NotificationCaptureService.KEY_EVENTS,"[]")?:"[]"
    var malformed=false
    val source=try {
      if(current==null)JSONArray(raw) else {
        val envelope=JSONObject(raw)
        if(envelope.optInt("version")!=2)malformed=true
        envelope.getJSONArray("events")
      }
    }catch(_:Exception){malformed=true;JSONArray()}
    val kept=JSONArray();val seen=mutableMapOf<String,String>()
    for(i in 0 until source.length()) {
      val item=source.opt(i)
      if(!valid(item)){malformed=true;continue}
      val event=item as JSONObject;val id=event.getString("event_id");val serialized=event.toString()
      if(seen.containsKey(id)){if(seen[id]!=serialized)malformed=true;continue}
      seen[id]=serialized;kept.put(event)
    }
    if(malformed) {
      val sourceKey=if(current==null)NotificationCaptureService.KEY_EVENTS else KEY_INBOX
      var already=false
      for(i in 0 until recovery.length())if(recovery.getJSONObject(i).optString("source_key")==sourceKey && recovery.getJSONObject(i).optString("raw")==raw)already=true
      if(!already)recovery.put(JSONObject().put("source_key",sourceKey).put("raw",raw).put("reason","invalid_notification_inbox").put("recorded_at_ms",System.currentTimeMillis()))
    }
    // The legacy key is never cleared: v2 makes it an immutable recovery source.
    if(current==null || malformed || damagedRecovery)write(prefs,kept,recovery)
    return Pair(kept,recovery)
  }
  fun snapshot(prefs:SharedPreferences):JSONObject=synchronized(NotificationCaptureService.QUEUE_LOCK) {
    try {
      val (events,recovery)=read(prefs)
      JSONObject().put("version",2).put("events",events).put("state",if(failure!=null)"unavailable" else if(recovery.length()>0)"needs_recovery" else "ready")
        .put("recovery_count",recovery.length()).put("error",failure?:if(recovery.length()>0)"Há conteúdo de notificações preservado para recuperação." else "")
    }catch(_:Exception){
      failure="Não foi possível ler ou persistir a fila de notificações. Preserve os dados locais e tente recuperar."
      JSONObject().put("version",2).put("events",JSONArray()).put("state","unavailable").put("recovery_count",0).put("error",failure)
    }
  }
  fun append(prefs:SharedPreferences,event:JSONObject)=synchronized(NotificationCaptureService.QUEUE_LOCK) {
    try {
      check(valid(event)){"Evento nativo inválido"}
      val (events,_)=read(prefs)
      for(i in 0 until events.length())if(events.getJSONObject(i).getString("event_id")==event.getString("event_id"))return@synchronized
      events.put(event);write(prefs,events)
    }catch(error:Exception){failure="A notificação não pôde ser armazenada. Confira a recuperação da fila local.";throw error}
  }
  fun markSynced(prefs:SharedPreferences,id:String)=synchronized(NotificationCaptureService.QUEUE_LOCK) {
    try {
      val (events,_)=read(prefs);val kept=JSONArray()
      for(i in 0 until events.length()){val item=events.getJSONObject(i);if(item.getString("event_id")!=id)kept.put(item)}
      write(prefs,kept)
    }catch(error:Exception){failure="A confirmação da notificação não foi persistida. A tentativa permanece preservada.";throw error}
  }
  fun export(prefs:SharedPreferences):String=synchronized(NotificationCaptureService.QUEUE_LOCK) {
    JSONObject().put("version",2).put("legacy_source",prefs.getString(NotificationCaptureService.KEY_EVENTS,null))
      .put("active_source",prefs.getString(KEY_INBOX,null)).put("recovery_source",prefs.getString(KEY_RECOVERY,null)).toString(2)
  }
}
