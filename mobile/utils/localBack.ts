import {createContext, useContext, useLayoutEffect, useRef} from 'react';
import {Alert, cancelDialog, dialogSnapshot} from './appAlert';
import {isUserOperationBusy, isUserOperationSync, runUserAction} from './userOperation';

const handlers = new Map<symbol,{priority:number; action:()=>unknown}>();
let choiceBack:(()=>void)|null=null;
const activeScope=createContext(true);
export const LocalBackScope=activeScope.Provider;
export function setChoiceBack(action:(()=>void)|null) {choiceBack=action;}
// One hierarchy regardless of native listener registration order. A higher
// priority editor runs before its page's parent navigation.
export function useLocalBack(action:()=>unknown,enabled=true,priority=10) {
  const active=useContext(activeScope);
  const identity=useRef(Symbol('local-back')),latest=useRef(action);
  useLayoutEffect(()=>{latest.current=action;});
  useLayoutEffect(()=>{
    if(!enabled||!active)return;
    const key=identity.current;handlers.set(key,{priority,action:()=>latest.current()});
    return()=>{handlers.delete(key);};
  },[enabled,priority,active]);
}
const reportBackError=(error:unknown)=>{
  Alert.alert('Rascunho preservado',error instanceof Error?error.message:'Não foi possível voltar. Tente novamente.');
};
export function dispatchLocalBack():boolean {
  if(isUserOperationBusy()&&!isUserOperationSync())return true;
  if(dialogSnapshot()){cancelDialog();return true;}
  if(choiceBack){choiceBack();return true;}
  const current=[...handlers.values()].reverse().sort((a,b)=>b.priority-a.priority)[0];
  if(!current)return false;
  if(isUserOperationSync()){
    try{
      const result=current.action();
      if(result!=null&&typeof (result as PromiseLike<unknown>).then==='function'){
        void Promise.resolve(result).catch(reportBackError);
      }
    }catch(error){reportBackError(error);}
    return true;
  }
  runUserAction(current.action,'Voltando…',reportBackError);
  return true;
}
export function protectUnsavedBack(dirty:boolean,back:()=>unknown) {
  if(!dirty)return back();
  Alert.alert('Rascunho nesta tela','Seus campos ainda não foram salvos. Salve ou use “Excluir rascunho” antes de sair; suas edições foram preservadas.',[{text:'Continuar editando',style:'cancel'}]);
}
