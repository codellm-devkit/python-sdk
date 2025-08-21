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

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

import javax.servlet.ServletConfig;
import javax.servlet.ServletException;
import javax.servlet.ServletOutputStream;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

@WebServlet(name = "PingReentryServlet", urlPatterns = { "/servlet/PingReentryServlet" })
public class PingReentryServlet extends HttpServlet {

    private static final long serialVersionUID = -2536027021580175706L;
    
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
    public void doGet(HttpServletRequest req, HttpServletResponse res) throws ServletException, IOException {
        try {
            res.setContentType("text/html");

            // The following 2 lines are the difference between PingServlet and
            // PingServletWriter
            // the latter uses a PrintWriter for output versus a binary output
            // stream.
            ServletOutputStream out = res.getOutputStream();
            // java.io.PrintWriter out = res.getWriter();
            int numReentriesLeft; 
            int sleepTime;
            
            if(req.getParameter("numReentries") != null){
                numReentriesLeft = Integer.parseInt(req.getParameter("numReentries"));
            } else {
                numReentriesLeft = 0;
            }
            
            if(req.getParameter("sleep") != null){
                sleepTime = Integer.parseInt(req.getParameter("sleep"));
            } else {
                sleepTime = 0;
            }
                
            if(numReentriesLeft <= 0) {
                Thread.sleep(sleepTime);
                out.println(numReentriesLeft);
            } else {
                String hostname = req.getServerName();
                int port = req.getServerPort();
                req.getContextPath();
                int saveNumReentriesLeft = numReentriesLeft;
                int nextNumReentriesLeft = numReentriesLeft - 1;
                
                // Recursively call into the same server, decrementing the counter by 1.
                String url = "http://" +  hostname + ":" + port + "/" + req.getRequestURI() + 
                        "?numReentries=" +  nextNumReentriesLeft +
                        "&sleep=" + sleepTime;
                URL obj = new URL(url);
                HttpURLConnection con = (HttpURLConnection) obj.openConnection();
                con.setRequestMethod("GET");
                con.setRequestProperty("User-Agent", "Mozilla/5.0");
                
                //Append the recursion count to the response and return it.
                BufferedReader in = new BufferedReader(
                        new InputStreamReader(con.getInputStream()));
                String inputLine;
                StringBuffer response = new StringBuffer();
         
                while ((inputLine = in.readLine()) != null) {
                    response.append(inputLine);
                }
                in.close();
                
                Thread.sleep(sleepTime);
                out.println(saveNumReentriesLeft + response.toString());
            }
        } catch (Exception e) {
            //Log.error(e, "PingReentryServlet.doGet(...): general exception caught");
            res.sendError(500, e.toString());

        }
    }

    /**
     * returns a string of information about the servlet
     *
     * @return info String: contains info about the servlet
     **/
    @Override
    public String getServletInfo() {
        return "Basic dynamic HTML generation through a servlet";
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

    }
}
