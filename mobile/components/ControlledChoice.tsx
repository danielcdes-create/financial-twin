import React, {useId, useLayoutEffect, useRef} from 'react';
import {StyleSheet, Text, View} from 'react-native';
import {Pressable} from './InteractionControls';
import {useChoiceOverlay} from './ChoiceOverlay';
import {readableText} from '../utils/responsiveLayout';

type Props={
  label:string;
  value:string;
  options:string[];
  onChange:(value:string)=>void | Promise<unknown>;
  display?:(value:string)=>string;
  placeholder?:string;
  hint?:string;
  allowClear?:boolean;
  disabled?:boolean;
};

export default function ControlledChoice({label,value,options,onChange,display=(item)=>item,placeholder,hint,allowClear=true,disabled=false}:Props){
  const overlay=useChoiceOverlay(),owner=useId(),latest=useRef({onChange,options,disabled,allowClear});
  useLayoutEffect(()=>{latest.current={onChange,options,disabled,allowClear};},[onChange,options,disabled,allowClear]);
  const {close}=overlay;
  useLayoutEffect(()=>()=>close(owner),[close,owner]);
  useLayoutEffect(()=>{if(disabled)close(owner);},[close,owner,disabled]);
  const open=()=>{if(disabled)return;overlay.open({owner,label,value,allowClear,
    options:[...new Set(options.filter(item=>typeof item==='string'&&item.length>0))].map(item=>({value:item,label:String(display(item)||item)})),
    onSelect:selected=>{if(!latest.current.disabled&&(selected===''?latest.current.allowClear:latest.current.options.includes(selected)))return latest.current.onChange(selected);}});};
  return <View style={s.field}>
    <Text style={s.label}>{label}</Text>
    <Pressable accessibilityRole="button" accessibilityLabel={`Selecionar ${label}`} accessibilityState={{disabled}} disabled={disabled} onPress={open} style={[s.selector,disabled&&s.disabled]}>
      <Text style={[value?s.value:s.placeholder,readableText]}>{value?display(value):(placeholder||`Selecione ${label.toLocaleLowerCase('pt-BR')}`)}</Text>
      <Text style={s.chevron}>⌄</Text>
    </Pressable>
    {!!hint&&<Text style={s.hint}>{hint}</Text>}
  </View>;
}

const s=StyleSheet.create({field:{marginBottom:16},label:{color:'#AFC1BF',fontSize:12,fontWeight:'600',marginBottom:6},selector:{minHeight:48,backgroundColor:'#142326',borderRadius:12,borderWidth:1,borderColor:'#263D40',paddingHorizontal:13,paddingVertical:10,flexDirection:'row',alignItems:'center',justifyContent:'space-between'},value:{color:'#F5F8F6',fontSize:15,flex:1,minWidth:0},placeholder:{color:'#92A4A2',fontSize:15,flex:1,minWidth:0},chevron:{color:'#67DFC3',fontSize:20,marginLeft:8},hint:{fontSize:10,lineHeight:15,color:'#92A4A2',marginTop:5},disabled:{opacity:.45}});
