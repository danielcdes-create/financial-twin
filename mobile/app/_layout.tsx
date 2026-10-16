import { Stack } from 'expo-router';
import { SQLiteProvider } from '../utils/queueDb';
import { StatusBar } from 'expo-status-bar';
import React, {useSyncExternalStore} from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import AuthProvider, {useAuth} from '../components/AuthProvider';
import AppDialogHost from '../components/AppDialogHost';
import UserOperationBoundary from '../components/UserOperationBoundary';
import {subscribeUserOperation, userOperationSnapshot} from '../utils/userOperation';

// Expo closes the database and unmounts its children when onInit changes.
// Navigation must never recreate this callback and discard the accepted session.
const initializeQueue: NonNullable<React.ComponentProps<typeof SQLiteProvider>['onInit']> = async db => {
    await db.execAsync('CREATE TABLE IF NOT EXISTS event_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, payload TEXT NOT NULL, local_uri TEXT, filename TEXT, mime_type TEXT, created_at TEXT NOT NULL)');
    await db.execAsync('CREATE TABLE IF NOT EXISTS queue_state (event_id TEXT PRIMARY KEY NOT NULL, attempt_count INTEGER NOT NULL, next_attempt_at TEXT, last_error TEXT NOT NULL, quarantined INTEGER NOT NULL DEFAULT 0)');
    await db.execAsync('CREATE TABLE IF NOT EXISTS review_drafts (provisional_id TEXT PRIMARY KEY NOT NULL, item TEXT NOT NULL, draft TEXT NOT NULL, updated_at TEXT NOT NULL)');
    await db.execAsync('CREATE TABLE IF NOT EXISTS batch_drafts (batch_id TEXT PRIMARY KEY NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL)');
    await db.execAsync('CREATE TABLE IF NOT EXISTS queue_acknowledgements (event_id TEXT PRIMARY KEY NOT NULL, identity TEXT NOT NULL, result TEXT NOT NULL, confirmed_at TEXT NOT NULL)');
    await db.execAsync('CREATE TABLE IF NOT EXISTS preserved_drafts (draft_id TEXT PRIMARY KEY NOT NULL, scope TEXT NOT NULL, item_id TEXT NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL)');
    await db.execAsync('CREATE TABLE IF NOT EXISTS review_commands (command_key TEXT PRIMARY KEY NOT NULL, path TEXT NOT NULL, body TEXT NOT NULL, result TEXT, updated_at TEXT NOT NULL)');
    await db.execAsync('CREATE TABLE IF NOT EXISTS voice_drafts (draft_key TEXT PRIMARY KEY NOT NULL, state TEXT NOT NULL, updated_at TEXT NOT NULL)');
};

export default function RootLayout() {
  return <SQLiteProvider databaseName="financial-twin.db" onInit={initializeQueue}><SafeAreaProvider><StatusBar hidden /><AuthProvider><UserOperationBoundary><RootNavigator/></UserOperationBoundary><AppDialogHost/></AuthProvider></SafeAreaProvider></SQLiteProvider>;
}

function RootNavigator() {
  const {authenticated} = useAuth();
  const operation = useSyncExternalStore(subscribeUserOperation, userOperationSnapshot, userOperationSnapshot);
  return <Stack screenOptions={{headerShown: false, navigationBarHidden: true, navigationBarTranslucent: true,
    gestureEnabled: !operation.busy && !operation.dialogOpen}}>
    <Stack.Protected guard={authenticated}>
      <Stack.Screen name="index"/>
      <Stack.Screen name="account"/>
      <Stack.Screen name="handle-share"/>
      <Stack.Screen name="compromissos/mes/[month]"/>
    </Stack.Protected>
    <Stack.Protected guard={!authenticated}>
      <Stack.Screen name="login"/>
    </Stack.Protected>
  </Stack>;
}
