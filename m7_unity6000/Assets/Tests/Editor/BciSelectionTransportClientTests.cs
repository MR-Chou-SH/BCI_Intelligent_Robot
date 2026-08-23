using System;
using System.Collections;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading.Tasks;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.TestTools;

namespace BCIIntelligentRobot.Tests
{
    public class BciSelectionTransportClientTests
    {
        [TestCase(SocketError.TimedOut)]
        [TestCase(SocketError.WouldBlock)]
        public void IdleReadSocketErrors_DoNotBreakConnectedLoop(SocketError socketError)
        {
            var exception = new IOException("idle read", new SocketException((int)socketError));

            Assert.That(BciSelectionTransportClient.IsTransientReadException(exception), Is.True);
        }

        [TestCase(SocketError.ConnectionReset)]
        [TestCase(SocketError.NetworkDown)]
        public void RealSocketErrors_AreNotTreatedAsIdleReads(SocketError socketError)
        {
            var exception = new IOException("socket failure", new SocketException((int)socketError));

            Assert.That(BciSelectionTransportClient.IsTransientReadException(exception), Is.False);
        }

        [Test]
        public void RemoteEof_ReconnectsToTheNextServerConnection()
        {
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            GameObject bindingObject = new GameObject("BciSsvepTargetBindingTests");
            GameObject transportObject = new GameObject("BciSelectionTransportClientTests");
            BciSsvepTargetBinding binding = bindingObject.AddComponent<BciSsvepTargetBinding>();
            BciSelectionTransportClient transport = transportObject.AddComponent<BciSelectionTransportClient>();

            try
            {
                int port = ((IPEndPoint)listener.LocalEndpoint).Port;
                Task<TcpClient> firstAccept = listener.AcceptTcpClientAsync();
                transport.Initialize(binding, "127.0.0.1", port);
                Assert.That(firstAccept.Wait(1000), Is.True, "Transport must connect to the first server listener.");

                using (TcpClient firstConnection = firstAccept.Result)
                {
                    firstConnection.Close();
                }

                Task<TcpClient> secondAccept = listener.AcceptTcpClientAsync();
                Assert.That(secondAccept.Wait(2000), Is.True,
                    "Remote TCP close must return from ConnectedLoop and trigger reconnect.");
                secondAccept.Result.Close();
            }
            finally
            {
                listener.Stop();
                UnityEngine.Object.DestroyImmediate(transportObject);
                UnityEngine.Object.DestroyImmediate(bindingObject);
            }
        }

        [UnityTest]
        public IEnumerator SelectionAckThenRemoteEof_ReconnectsToTheNextServerConnection()
        {
            var listener = new TcpListener(IPAddress.Loopback, 0);
            listener.Start();
            GameObject bindingObject = new GameObject("BciSsvepTargetBindingAckTests");
            GameObject transportObject = new GameObject("BciSelectionTransportClientAckTests");
            BciSsvepTargetBinding binding = bindingObject.AddComponent<BciSsvepTargetBinding>();
            BciSelectionTransportClient transport = transportObject.AddComponent<BciSelectionTransportClient>();

            try
            {
                int port = ((IPEndPoint)listener.LocalEndpoint).Port;
                Task<TcpClient> firstAccept = listener.AcceptTcpClientAsync();
                transport.Initialize(binding, "127.0.0.1", port);
                yield return WaitForTask(firstAccept, 1000);
                Assert.That(firstAccept.IsCompleted, Is.True, "Transport must connect to the first server listener.");

                using (TcpClient firstConnection = firstAccept.Result)
                {
                    NetworkStream stream = firstConnection.GetStream();
                    stream.ReadTimeout = 100;
                    SendLine(stream, "{\"protocolVersion\":1,\"messageType\":\"selection_open\",\"selectionId\":\"transport-eof-ack\"}");

                    string ack = null;
                    var textBuffer = new StringBuilder();
                    DateTime ackDeadline = DateTime.UtcNow.AddSeconds(2);
                    while (ack == null && DateTime.UtcNow < ackDeadline)
                    {
                        ack = ReadLineIfAvailable(stream, textBuffer);
                        yield return null;
                    }

                    Assert.That(ack, Does.Contain("\"messageType\":\"selection_ack\""));
                    Assert.That(ack, Does.Contain("\"accepted\":true"));
                }

                Task<TcpClient> secondAccept = listener.AcceptTcpClientAsync();
                yield return WaitForTask(secondAccept, 2000);
                Assert.That(secondAccept.IsCompleted, Is.True,
                    "After an ACK and remote TCP close, transport must reconnect to the next listener.");
                secondAccept.Result.Close();
            }
            finally
            {
                listener.Stop();
                UnityEngine.Object.DestroyImmediate(transportObject);
                UnityEngine.Object.DestroyImmediate(bindingObject);
            }
        }

        private static IEnumerator WaitForTask<T>(Task<T> task, int timeoutMilliseconds)
        {
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMilliseconds);
            while (!task.IsCompleted && DateTime.UtcNow < deadline)
                yield return null;
        }

        private static void SendLine(NetworkStream stream, string line)
        {
            byte[] bytes = Encoding.UTF8.GetBytes(line + "\n");
            stream.Write(bytes, 0, bytes.Length);
        }

        private static string ReadLineIfAvailable(NetworkStream stream, StringBuilder buffer)
        {
            if (!stream.DataAvailable)
                return null;

            byte[] bytes = new byte[4096];
            int count = stream.Read(bytes, 0, bytes.Length);
            if (count == 0)
                return null;
            buffer.Append(Encoding.UTF8.GetString(bytes, 0, count));
            string all = buffer.ToString();
            int newline = all.IndexOf('\n');
            if (newline < 0)
                return null;
            string line = all.Substring(0, newline).TrimEnd('\r');
            buffer.Remove(0, newline + 1);
            return line;
        }
    }
}
