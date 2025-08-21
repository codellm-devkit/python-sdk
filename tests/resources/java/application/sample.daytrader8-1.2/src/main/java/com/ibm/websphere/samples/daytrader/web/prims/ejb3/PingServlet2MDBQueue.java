/**
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.web.prims.ejb3;

import java.io.IOException;

import javax.annotation.Resource;
import javax.jms.Connection;
import javax.jms.ConnectionFactory;
import javax.jms.JMSContext;
import javax.jms.Queue;
import javax.jms.TextMessage;
import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;

/**
 * This primitive is designed to run inside the TradeApplication and relies upon
 * the {@link com.ibm.websphere.samples.daytrader.util.TradeConfig} class to set
 * configuration parameters. PingServlet2MDBQueue tests key functionality of a
 * servlet call to a post a message to an MDB Queue. The TradeBrokerMDB receives
 * the message This servlet makes use of the MDB EJB
 * {@link com.ibm.websphere.samples.daytrader.ejb3.DTBroker3MDB} by posting a
 * message to the MDB Queue
 */
@WebServlet(name = "ejb3.PingServlet2MDBQueue", urlPatterns = { "/ejb3/PingServlet2MDBQueue" })
public class PingServlet2MDBQueue extends HttpServlet {

    private static final long serialVersionUID = 2637271552188745216L;

    private static String initTime;

    private static int hitCount;

    @Resource(name = "jms/QueueConnectionFactory", authenticationType = javax.annotation.Resource.AuthenticationType.APPLICATION)
    private ConnectionFactory queueConnectionFactory;

    // TODO: Glassfish does not like this - change to lookup?
    @Resource(name = "jms/TradeBrokerQueue")
    private Queue tradeBrokerQueue;

    @Override
    public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        doGet(req, res);
    }

    @Override
    public void doGet(HttpServletRequest req, HttpServletResponse res) throws IOException, ServletException {

        res.setContentType("text/html");
        java.io.PrintWriter out = res.getWriter();
        // use a stringbuffer to avoid concatenation of Strings
        StringBuffer output = new StringBuffer(100);
        output.append("<html><head><title>PingServlet2MDBQueue</title></head>"
                + "<body><HR><FONT size=\"+2\" color=\"#000066\">PingServlet2MDBQueue<BR></FONT>" + "<FONT size=\"-1\" color=\"#000066\">"
                + "Tests the basic operation of a servlet posting a message to an EJB MDB through a JMS Queue.<BR>"
                + "<FONT color=\"red\"><B>Note:</B> Not intended for performance testing.</FONT>");

        try {
            Connection conn = queueConnectionFactory.createConnection();

            try {
                TextMessage message = null;
                int iter = TradeConfig.getPrimIterations();
                for (int ii = 0; ii < iter; ii++) {
                    /*Session sess = conn.createSession(false, Session.AUTO_ACKNOWLEDGE);
                    try {
                        MessageProducer producer = sess.createProducer(tradeBrokerQueue);

                        message = sess.createTextMessage();

                        String command = "ping";
                        message.setStringProperty("command", command);
                        message.setLongProperty("publishTime", System.currentTimeMillis());
                        message.setText("Ping message for queue java:comp/env/jms/TradeBrokerQueue sent from PingServlet2MDBQueue at " + new java.util.Date());
                        producer.send(message);
                    } finally {
                        sess.close();
                    }*/
                	
                	JMSContext context = queueConnectionFactory.createContext();
            		
            		message = context.createTextMessage();

            		message.setStringProperty("command", "ping");
                    message.setLongProperty("publishTime", System.currentTimeMillis());
                    message.setText("Ping message for queue java:comp/env/jms/TradeBrokerQueue sent from PingServlet2MDBQueue at " + new java.util.Date());
              		
            		context.createProducer().send(tradeBrokerQueue, message);
                }

                // write out the output
                output.append("<HR>initTime: ").append(initTime);
                output.append("<BR>Hit Count: ").append(hitCount++);
                output.append("<HR>Posted Text message to java:comp/env/jms/TradeBrokerQueue destination");
                output.append("<BR>Message: ").append(message);
                output.append("<BR><BR>Message text: ").append(message.getText());
                output.append("<BR><HR></FONT></BODY></HTML>");
                out.println(output.toString());

            } catch (Exception e) {
                Log.error("PingServlet2MDBQueue.doGet(...):exception posting message to TradeBrokerQueue destination ");
                throw e;
            } finally {
                conn.close();
            }
        } // this is where I actually handle the exceptions
        catch (Exception e) {
            Log.error(e, "PingServlet2MDBQueue.doGet(...): error");
            res.sendError(500, "PingServlet2MDBQueue.doGet(...): error, " + e.toString());

        }
    }

    @Override
    public String getServletInfo() {
        return "web primitive, configured with trade runtime configs, tests Servlet to Session EJB path";

    }

    @Override
    public void init(ServletConfig config) throws ServletException {
        super.init(config);
        hitCount = 0;
        initTime = new java.util.Date().toString();
    }

}
