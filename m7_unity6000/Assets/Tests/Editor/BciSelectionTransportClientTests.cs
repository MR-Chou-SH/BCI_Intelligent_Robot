using System.Net;
using System.Net.Sockets;
using System.Threading.Tasks;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using NUnit.Framework;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public class BciSelectionTransportClientTests
    {
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
                Object.DestroyImmediate(transportObject);
                Object.DestroyImmediate(bindingObject);
            }
        }
    }
}
