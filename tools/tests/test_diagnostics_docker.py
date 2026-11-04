"""Actual Docker stream test with a disposable console, never game data."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from distribution import diagnostics


@unittest.skipUnless(os.environ.get("HOA_TEST_DOCKER")=="1" and os.name!="nt", "Explicit isolated Docker test")
class DockerConsoleTests(unittest.TestCase):
    def test_commands_are_consumed_and_restore_even_with_stdin_once(self):
        class DockerHTTP(http.client.HTTPConnection):
            def connect(self):
                self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
                self.sock.connect("/var/run/docker.sock")
        name="hoa-trace-test-"+uuid.uuid4().hex[:12]
        script='''mkdir -p /opt/trinitycore/etc
printf 'Logger.network.opcode = 5,Console Server\\nAppender.Console=1,3,0\\nAddonChannel=1\\nHordes.Enable=1\\nLoginDatabaseInfo=private-fixture-password\\n' > /opt/trinitycore/etc/worldserver.conf
opcode=5; console=3
while IFS= read -r line; do
case "$line" in
'server set loglevel l network.opcode 1') opcode=1;;
'server set loglevel a Console 1') console=1;;
'server set loglevel l network.opcode 5') opcode=5;;
'server set loglevel a Console 3') console=3;;
esac
printf '%s/%s' "$opcode" "$console" > /tmp/hoa-levels
if [ "$opcode/$console" = 1/1 ]; then
echo 'C->S: classic opcode 0x2D002B translated to [CMSG_CHAT_ADDON_MESSAGE_TARGETED 0x2C002B (123)]'
fi
done'''
        connection=DockerHTTP("docker",timeout=5)
        identity=None
        try:
            payload=dict(Image="mysql:8.0",Entrypoint=["bash","-c",script],OpenStdin=True,StdinOnce=True,Tty=True,
                         HostConfig=dict(NetworkMode="none"))
            connection.request("POST","/containers/create?name="+name,json.dumps(payload),{"Content-Type":"application/json"})
            response=connection.getresponse();body=response.read()
            self.assertEqual(response.status,201,body)
            identity=json.loads(body)["Id"]
            subprocess.run(["docker","start",identity],check=True,stdout=subprocess.DEVNULL)
            output=diagnostics.collect("unix:///var/run/docker.sock",identity,seconds=.3)
            self.assertIn("CMSG_CHAT_ADDON_MESSAGE_TARGETED",output)
            self.assertNotIn("private-fixture-password",output)
            levels=subprocess.check_output(["docker","exec",identity,"cat","/tmp/hoa-levels"],text=True)
            self.assertEqual(levels,"5/3")
            state=subprocess.check_output(["docker","inspect","--format","{{.State.Running}} {{.Config.StdinOnce}}",identity],text=True).strip()
            self.assertEqual(state,"true true")
            def fail(_): raise RuntimeError("simulated cancellation")
            with self.assertRaisesRegex(RuntimeError,"simulated cancellation"):
                diagnostics.collect("unix:///var/run/docker.sock",identity,fail,seconds=.3)
            self.assertEqual(subprocess.check_output(["docker","exec",identity,"cat","/tmp/hoa-levels"],text=True),"5/3")
            print("PASS: actual Docker console input, clean detach and restoration after simulated cancellation")
        finally:
            connection.close()
            if identity: subprocess.run(["docker","rm","--force",identity],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
