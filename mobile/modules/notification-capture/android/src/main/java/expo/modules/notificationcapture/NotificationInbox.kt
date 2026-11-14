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
  const val MAX_INBOX_EVENTS=500
  const val MAX_RECOVERY_ENTRIES=16
  const val MAX_RECOVERY_ITEM_CHARS=16384
  @Volatile private var failure:String?=null

  private fun capRecoveryRaw(raw:String):String=if(raw.length<=MAX_RECOVERY_ITEM_CHARS)raw else raw.substring(0,MAX_RECOVERY_ITEM_CHARS)
  private fun trimArray(source:JSONArray,maxItems:Int):JSONArray {
    if(source.length()<=maxItems)return source
    val trimmed=JSONArray()
    val start=source.length()-maxItems
    for(i in start until source.length())trimmed.put(source.opt(i))
    return trimmed
  }
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
  private fun write(prefs:SharedPreferences,events:JSONArray,recovery:JSONArray?=null,droppedCount:Int=0) {
    val newlyDropped=(events.length()-MAX_INBOX_EVENTS).coerceAtLeast(0)
    val totalDropped=droppedCount.coerceAtLeast(0)+newlyDropped
    val boundedEvents=trimArray(events,MAX_INBOX_EVENTS)
    val envelope=JSONObject().put("version",2).put("events",boundedEvents).put("dropped_count",totalDropped)
    val edit=prefs.edit().putString(KEY_INBOX,envelope.toString())
    if(recovery!=null)edit.putString(KEY_RECOVERY,trimArray(recovery,MAX_RECOVERY_ENTRIES).toString())
    check(edit.commit()){ "Falha ao persistir a fila de notificações. A origem permanece preservada." }
    failure=null
  }
  private fun read(prefs:SharedPreferences):Triple<JSONArray,JSONArray,Int> {
    val recoveryRaw=prefs.getString(KEY_RECOVERY,"[]")?:"[]"
    var damagedRecovery=false
    val recoverySource=try{JSONArray(recoveryRaw)}catch(_:Exception){damagedRecovery=true;JSONArray()}
    val recovery=JSONArray()
    for(i in 0 until recoverySource.length()){
      val item=recoverySource.opt(i)
      if(item is JSONObject && item.opt("source_key") is String && item.opt("raw") is String){
        val existingRaw=item.optString("raw")
        if(existingRaw.length>MAX_RECOVERY_ITEM_CHARS){
          recovery.put(JSONObject(item.toString()).put("raw",capRecoveryRaw(existingRaw)).put("truncated",true))
          damagedRecovery=true
        }else recovery.put(item)
      } else damagedRecovery=true
    }
    if(damagedRecovery){
      val cappedDiag=capRecoveryRaw(recoveryRaw)
      var alreadyDiag=false
      for(i in 0 until recovery.length())if(recovery.getJSONObject(i).optString("source_key")==KEY_RECOVERY && recovery.getJSONObject(i).optString("raw")==cappedDiag)alreadyDiag=true
      if(!alreadyDiag && recoveryRaw!="[]" && !(recoverySource.length()>0 && recovery.length()==recoverySource.length())){
        val diag=JSONObject().put("source_key",KEY_RECOVERY).put("raw",cappedDiag).put("reason","invalid_recovery_archive").put("recorded_at_ms",System.currentTimeMillis())
        if(recoveryRaw.length>MAX_RECOVERY_ITEM_CHARS)diag.put("truncated",true)
        recovery.put(diag)
      }
    }
    val current=prefs.getString(KEY_INBOX,null)
    val raw=current?:prefs.getString(NotificationCaptureService.KEY_EVENTS,"[]")?:"[]"
    var malformed=false
    var priorDropped=0
    val source=try {
      if(current==null)JSONArray(raw) else {
        val envelope=JSONObject(raw)
        if(envelope.optInt("version")!=2)malformed=true
        priorDropped=envelope.optInt("dropped_count",0).coerceAtLeast(0)
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
      val cappedRaw=capRecoveryRaw(raw)
      var already=false
      for(i in 0 until recovery.length())if(recovery.getJSONObject(i).optString("source_key")==sourceKey && recovery.getJSONObject(i).optString("raw")==cappedRaw)already=true
      if(!already){
        val entry=JSONObject().put("source_key",sourceKey).put("raw",cappedRaw).put("reason","invalid_notification_inbox").put("recorded_at_ms",System.currentTimeMillis())
        if(raw.length>MAX_RECOVERY_ITEM_CHARS)entry.put("truncated",true)
        recovery.put(entry)
      }
    }
    val newlyDropped=(kept.length()-MAX_INBOX_EVENTS).coerceAtLeast(0)
    val totalDropped=priorDropped+newlyDropped
    val boundedKept=trimArray(kept,MAX_INBOX_EVENTS)
    val boundedRecovery=trimArray(recovery,MAX_RECOVERY_ENTRIES)
    // The legacy key is never cleared: v2 makes it an immutable recovery source.
    if(current==null || malformed || damagedRecovery || kept.length()>MAX_INBOX_EVENTS || recovery.length()>MAX_RECOVERY_ENTRIES)write(prefs,boundedKept,boundedRecovery,totalDropped)
    return Triple(boundedKept,boundedRecovery,totalDropped)
  }
  fun snapshot(prefs:SharedPreferences):JSONObject=synchronized(NotificationCaptureService.QUEUE_LOCK) {
    try {
      val (events,recovery,droppedCount)=read(prefs)
      JSONObject().put("version",2).put("events",events).put("state",if(failure!=null)"unavailable" else if(recovery.length()>0)"needs_recovery" else "ready")
        .put("recovery_count",recovery.length()).put("dropped_count",droppedCount)
        .put("error",failure?:if(recovery.length()>0)"Há conteúdo de notificações preservado para recuperação." else "")
    }catch(_:Exception){
      failure="Não foi possível ler ou persistir a fila de notificações. Preserve os dados locais e tente recuperar."
      JSONObject().put("version",2).put("events",JSONArray()).put("state","unavailable").put("recovery_count",0).put("dropped_count",0).put("error",failure)
    }
  }
  fun append(prefs:SharedPreferences,event:JSONObject)=synchronized(NotificationCaptureService.QUEUE_LOCK) {
    try {
      check(valid(event)){"Evento nativo inválido"}
      val (events,_,droppedCount)=read(prefs)
      for(i in 0 until events.length())if(events.getJSONObject(i).getString("event_id")==event.getString("event_id"))return@synchronized
      events.put(event);write(prefs,events,null,droppedCount)
    }catch(error:Exception){failure="A notificação não pôde ser armazenada. Confira a recuperação da fila local.";throw error}
  }
  fun markSynced(prefs:SharedPreferences,id:String)=synchronized(NotificationCaptureService.QUEUE_LOCK) {
    try {
      val (events,_,droppedCount)=read(prefs);val kept=JSONArray()
      for(i in 0 until events.length()){val item=events.getJSONObject(i);if(item.getString("event_id")!=id)kept.put(item)}
      write(prefs,kept,null,droppedCount)
    }catch(error:Exception){failure="A confirmação da notificação não foi persistida. A tentativa permanece preservada.";throw error}
  }
  fun export(prefs:SharedPreferences):String=synchronized(NotificationCaptureService.QUEUE_LOCK) {
    JSONObject().put("version",2).put("legacy_source",prefs.getString(NotificationCaptureService.KEY_EVENTS,null))
      .put("active_source",prefs.getString(KEY_INBOX,null)).put("recovery_source",prefs.getString(KEY_RECOVERY,null)).toString(2)
  }
}
