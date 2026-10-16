import {useAuth} from '../components/AuthProvider';

export default function LoginScreen() {
  return useAuth().loginScreen;
}
