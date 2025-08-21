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
package com.ibm.websphere.samples.daytrader.web.prims;

import java.io.IOException;
import java.io.PrintWriter;

import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import javax.servlet.http.HttpSession;

import com.ibm.websphere.samples.daytrader.util.Log;

/**
 *
 * PingHTTPSession3 tests the servers ability to manage and persist large
 * HTTPSession data objects. The servlet creates the large custom java object
 * {@link PingSession3Object}. This large session object is retrieved and stored
 * to the session on each user request. The default settings result in approx
 * 2024 bits being retrieved and stored upon each request.
 *
 */
@WebServlet(name = "PingSession3", urlPatterns = { "/servlet/PingSession3" })
public class PingSession3 extends HttpServlet {
    private static final long serialVersionUID = -6129599971684210414L;
    private static int NUM_OBJECTS = 2;
    private static String initTime = null;
    private static int hitCount = 0;

    /**
     * forwards post requests to the doGet method Creation date: (11/6/2000
     * 10:52:39 AM)
     *
     * @param res
     *            javax.servlet.http.HttpServletRequest
     * @param res2
     *            javax.servlet.http.HttpServletResponse
     */
    @Override
    public void doPost(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        doGet(req, res);
    }

    /**
     * this is the main method of the servlet that will service all get
     * requests.
     *
     * @param request
     *            HttpServletRequest
     * @param responce
     *            HttpServletResponce
     **/
    @Override
    public void doGet(HttpServletRequest request, HttpServletResponse response) throws ServletException, IOException {

        PrintWriter out = response.getWriter();
        // Using a StringBuffer to output all at once.
        StringBuffer outputBuffer = new StringBuffer();
        HttpSession session = null;
        PingSession3Object[] sessionData;
        response.setContentType("text/html");

        // this is a general try/catch block. The catch block at the end of this
        // will forward the responce
        // to an error page if there is an exception
        try {

            try {
                session = request.getSession(true);
            } catch (Exception e) {
                Log.error(e, "PingSession3.doGet(...): error getting session");
                // rethrow the exception for handling in one place.
                throw e;

            }
            // Each PingSession3Object in the PingSession3Object array is 1K in
            // size
            // NUM_OBJECTS sets the size of the array to allocate and thus set
            // the size in KBytes of the session object
            // NUM_OBJECTS can be initialized by the servlet
            // Here we check for the request parameter to change the size and
            // invalidate the session if it exists
            // NOTE: Current user sessions will remain the same (i.e. when
            // NUM_OBJECTS is changed, all user thread must be restarted
            // for the change to fully take effect

            String num_objects;
            if ((num_objects = request.getParameter("num_objects")) != null) {
                // validate input
                try {
                    int x = Integer.parseInt(num_objects);
                    if (x > 0) {
                        NUM_OBJECTS = x;
                    }
                } catch (Exception e) {
                    Log.error(e, "PingSession3.doGet(...): input should be an integer, input=" + num_objects);
                } // revert to current value on exception

                outputBuffer.append("<html><head> Session object size set to " + NUM_OBJECTS + "K bytes </head><body></body></html>");
                if (session != null) {
                    session.invalidate();
                }
                out.print(outputBuffer.toString());
                out.close();
                return;
            }

            // Get the session data value
            sessionData = (PingSession3Object[]) session.getAttribute("sessiontest.sessionData");
            if (sessionData == null) {
                sessionData = new PingSession3Object[NUM_OBJECTS];
                for (int i = 0; i < NUM_OBJECTS; i++) {
                    sessionData[i] = new PingSession3Object();
                }
            }

            session.setAttribute("sessiontest.sessionData", sessionData);

            // Each PingSession3Object is about 1024 bits, there are 8 bits in a
            // byte.
            int num_bytes = (NUM_OBJECTS * 1024) / 8;
            response.setHeader("SessionTrackingTest-largeSessionData", num_bytes + "bytes");

            outputBuffer
                    .append("<html><head><title>Session Large Data Test</title></head><body><HR><BR><FONT size=\"+2\" color=\"#000066\">HTTP Session Test 3: Large Data<BR></FONT><FONT size=\"+1\" color=\"#000066\">Init time: ")
                    .append(initTime).append("</FONT><BR><BR>");
            hitCount++;
            outputBuffer.append("<B>Hit Count: ").append(hitCount)
                    .append("<BR>Session object updated. Session Object size = " + num_bytes + " bytes </B></body></html>");
            // output the Buffer to the printWriter.
            out.println(outputBuffer.toString());

        } catch (Exception e) {
            // log the excecption
            Log.error(e, "PingSession3.doGet(..l.): error.");
            // set the server responce to 500 and forward to the web app defined
            // error page
            response.sendError(500, "PingSession3.doGet(...): error. " + e.toString());
        }
    }

    /**
     * returns a string of information about the servlet
     *
     * @return info String: contains info about the servlet
     **/
    @Override
    public String getServletInfo() {
        return "HTTP Session Object: Tests management of a large custom session class";
    }

    /**
     * called when the class is loaded to initialize the servlet
     *
     * @param config
     *            ServletConfig:
     **/
    @Override
    public void init(ServletConfig config) throws ServletException {
        super.init(config);
        hitCount = 0;
        initTime = new java.util.Date().toString();

    }
}