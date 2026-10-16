import React from 'react';
import {ScrollView, View} from 'react-native';
import {SafeAreaView} from 'react-native-safe-area-context';
import {router} from 'expo-router';
import AccountSecurity from '../components/AccountSecurity';
import {AuthButton, authStyles} from '../components/AuthProvider';

export default function AccountScreen() {
  return <SafeAreaView style={authStyles.root}>
    <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{padding: 20, paddingBottom: 40}}>
      <View style={{width: '100%', maxWidth: 600, alignSelf: 'center', marginBottom: 20}}>
        <AuthButton secondary title="‹ Voltar" onPress={() => {if (router.canGoBack()) router.back(); else router.replace('/');}}/>
      </View>
      <AccountSecurity/>
    </ScrollView>
  </SafeAreaView>;
}
